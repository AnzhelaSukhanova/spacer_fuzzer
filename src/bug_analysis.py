import argparse
import traceback

from typing import Tuple

import instances
import log_analysis
import files
import constants

from main import check_satis
from instances import *

sys.setrecursionlimit(1000)


def is_same_res(instance: Instance, result: bool = False, message: str = None) -> bool:
    """
    Return True if solving an instance returns
    the expected result and False otherwise.
    """

    try:
        same_res = result == check_satis(instance, instance.get_group())
        instance.check_model()
        if instance.model_info[0] == unsat:
            same_res = True
        instance.model_info = (sat, 0)
    except AssertionError as err:
        same_res = repr(err) == message
    return same_res


def reduce_instance(seed: Instance, bug_instance: Instance,
                    message: str = None) -> Tuple[Instance, bool]:
    """Reduce the chc-system causing the problem."""

    print('Instance reducing')
    start_instance = deepcopy(bug_instance)
    instance = deepcopy(bug_instance)

    for i, clause in enumerate(instance.chc):
        print('Clause:', i)
        expr_queue = [clause]
        trans_number = -1
        expr_number = -1

        while len(expr_queue):
            cur_expr = expr_queue.pop()

            trans_number += 1
            expr_number += 1
            mutation = Mutation()
            mutation.set_remove_mutation(trans_number)

            try:
                reduced_chc = mutation.transform(instance)
                instance.set_chc(reduced_chc)
            except Exception:
                # print(traceback.format_exc())
                instance.set_chc(bug_instance.chc)
                for child in cur_expr.children():
                    expr_queue.append(child)
                continue

            is_eq = equivalence_check(seed.chc,
                                      instance.chc)
            if is_same_res(instance, message=message) and is_eq:
                bug_instance.set_chc(instance.chc)
                print('Reduced:', expr_number)
                trans_number -= 1
            else:
                instance.set_chc(bug_instance.chc)
                for child in cur_expr.children():
                    expr_queue.append(child)
                # print('Cannot be reduced:', trans_number)
    is_reduced = start_instance.chc.sexpr() != bug_instance.chc.sexpr()

    return bug_instance, is_reduced


def reduce_mut_chain(group: InstanceGroup, message: str = None) -> Instance:
    """
    Search for a reduced version of mutation chain that is
    the minimal set of bug-triggering transformations.
    """
    initial_size = len(group.instances)
    chunk_size = initial_size // 2

    while chunk_size:
        print(f'\nChunk size: {chunk_size}.')
        for i in range(initial_size - 1, 0, -chunk_size):
            from_ind = max(i - chunk_size + 1, 1)
            ind_chunk = range(from_ind, i + 1)
            try:
                new_group = undo_mutations(group, ind_chunk)
            except Z3Exception:
                continue
            new_instance = new_group[-1]
            if len(group.instances) != len(new_group.instances) and \
                    is_same_res(new_instance, message=message):
                group = new_group
                initial_size -= chunk_size
                print(f'{chunk_size} mutation can be removed.')

            # if chunk_size == 1:
            #     mut_type = group[ind_chunk[0]].mutation.type
            #     if mut_type.is_simplification():
            #         group[ind_chunk[0]] = reduce_simplify(group[ind_chunk[0] - 1], mut_type, message)
        chunk_size //= 2

    instance = group.pop()
    print(instance.mutation.get_chain())
    return instance


def undo_mutations(group: InstanceGroup, ind: range) -> InstanceGroup:
    """Undo the mutations from a given interval."""
    group_len = len(group.instances)
    new_group = InstanceGroup(len(instance_groups), group.filename)
    new_group.copy_info(group)
    for i in range(ind[0]):
        new_group.push(group[i])
        # mut = group[i].mutation
        # if i > 0:
        #     print(i, mut.prev_mutation.get_name(), mut.get_name())

    last_instance = new_group[ind[0] - 1]
    for i in range(ind[-1] + 1, group_len):
        mut_instance = Instance()
        mut_instance.mutation = deepcopy(group[i].mutation)
        mut = mut_instance.mutation
        try:
            mut.apply(last_instance, mut_instance)
        except AssertionError:
            return group
        new_group.push(mut_instance)
        last_instance = mut_instance

    return new_group


def reduce_simplify(instance: Instance, mut_type: str, message: str = None) -> Instance:
    """Reduce the applying of SIMPLIFY"""

    mut_instance = Instance(instance.group_id)
    mut = mut_instance.mutation
    mut.type = mut_type

    mut.path[0] = [i for i in range(len(instance.chc))]
    for i in range(len(instance.chc)):
        mut.path[0].remove(i)
        mut.apply(instance, mut_instance)
        if not is_same_res(mut_instance, message=message):
            mut.path[0].append(i)
        mut_instance.set_chc(instance.chc)
    if mut.path[0] == range(len(instance.chc)):
        mut.path[0] = None

    mut.apply(instance, mut_instance)
    return mut_instance


def reduce_declarations(instance: Instance):
    def get_pred_name(decl: str):
        name = decl.split(' ', 1)[0].rstrip()
        return name[1:-1] if name[0] == '|' else name

    group = instance.get_group()
    filename = group.filename
    decl_path = 'output/decl/' + filename
    with open(decl_path, 'r') as decl_file:
        declarations = decl_file.read()
    predicates = get_predicates(instance.chc)
    decl_chunks = declarations.split('(declare-fun ')
    i = 0
    for chunk in decl_chunks[1:]:
        i += 1
        pred_decl = get_pred_name(chunk)
        str_predicates = {str(item) for item in predicates}
        if pred_decl not in str_predicates:
            decl_chunks.pop(i)
            i -= 1
    new_declarations = '(declare-fun '.join(decl_chunks)
    return new_declarations


def reduce(filename: str = None, reduce_chain: bool = False, reduce_inst: bool = True) -> Instance:
    mutations, message, seed_name, output_dir = get_bug_info(filename)
    group_id = len(instance_groups)
    group = InstanceGroup(group_id, seed_name)
    if reduce_chain:
        group.restore(mutations, output_dir)
        mut_instance = group[-1]
        try:
            assert is_same_res(mut_instance, message=message)
        except AssertionError:
            print('Bug isn\'t reproduced:', filename)
            return None
        mut_instance = reduce_mut_chain(group, message)
    else:
        group.restore({}, output_dir)
        mut_system = parse_smt2_file(filename,
                                     ctx=ctx.current_ctx)
        mut_instance = Instance(group_id, mut_system)
        for entry in mutations:
            type_name = entry['type']
            type = mut_types[type_name]
            if type.is_solving_param():
                mut_instance.add_param(type)

        assert is_same_res(mut_instance, message=message), \
            'Incorrect mutant-restoration'

    reduce_dir = 'output/reduced/'
    if not os.path.exists(reduce_dir):
        os.mkdir(reduce_dir)
    for dir in map((lambda x: x + '/'), SEED_DIRS):
        for subdir in os.walk(dir):
            dir_path = reduce_dir + subdir[0]
            if not os.path.exists(dir_path):
                os.mkdir(dir_path)

    if reduce_inst:
        try:
            is_reduced = True
            while is_reduced:
                mut_instance, is_reduced = reduce_instance(group[0],
                                                           mut_instance,
                                                           message)
                declarations = reduce_declarations(mut_instance) \
                    if not is_reduced else None
                mut_instance.dump('output/reduced',
                                  seed_name,
                                  declarations=declarations,
                                  clear=False)

        except Exception:
            print(traceback.format_exc())

    return mut_instance


def get_bug_info(filename: str):
    with open(filename) as file:
        mut_line = file.readline()
        mut_line = mut_line[2:] if mut_line[0] == ';' else None
        message = file.readline()
        message = message[2:] if message[0] == ';' else None

    mutations = []
    if mut_line:
        if mut_line[0] == '[':
            mutations = json.loads(mut_line)
    output_dir = 'output'
    if not filename.startswith(tuple(SEED_DIRS)):
        chunks = filename.split('/')
        counter = 1
        for i, chunk in enumerate(chunks):
            if chunks[i + 1] not in SEED_DIRS:
                counter += 1
                output_dir = chunk if i == 0 else output_dir + '/' + chunk
            else:
                break
        filename = '/'.join(chunks[counter:])
        seed_name = '_'.join(filename.split('_')[:-1]) + '.smt2'
    else:
        seed_name = filename

    return mutations, message, seed_name, output_dir


def redo_mutations(filename: str):
    """Reproduce the bug."""
    mutations, message, seed_name, output_dir = get_bug_info(filename)
    id = 0
    group = InstanceGroup(id, seed_name)
    group.restore(mutations, output_dir)
    instance = group[-1]
    if is_same_res(instance, message=message):
        instance.dump('output/bugs',
                      group.filename,
                      0,
                      to_name=instance.id)
    else:
        print('Bug not found')


def deduplicate(bug_files=None, logfile: str = 'logfile'):
        
    if bug_files is None:
        bug_files = []
    bug_groups = defaultdict(set)
    if not bug_files:
        stats = log_analysis.prepare_data(logfile)
        ind = stats.df['status'] in {'bug', 'wrong_model'}
        for i, entry in stats.df.loc[ind].iterrows():
            if entry['status'] == 'bug' or entry['model_state'] == -1:
                filename = entry['filename']
                bug_id = str(int(entry['id']))
                bug_name = 'output/bugs/' + filename[:-5] + \
                           '_' + bug_id + '.smt2'
                bug_files.append(bug_name)

    for file in bug_files:
        mutations, message, seed_name, output_dir = get_bug_info(file)
        group_id = len(instance_groups)
        group = InstanceGroup(group_id, seed_name)
        group.restore(mutations, output_dir)
        instance = group[-1]
        if instance is None:
            continue

        params = instance.params
        reproduce = True
        if instance.mutation.number == 1:
            new_instance = reduce(file, True, False)
            if new_instance is not None:
                instance = new_instance
            else:
                reproduce = False
        last_mutation = instance.mutation
        if last_mutation.number <= 1 and reproduce:
            bug_groups[last_mutation.type.name.lower()].add(instance)

    for key in sorted(bug_groups):
        print(key, len(bug_groups[key]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bug_file',
                        nargs='?',
                        default=None)
    parser.add_argument('seed_file',
                        nargs='?',
                        default=None)
    parser.add_argument('-logfile',
                        nargs='?',
                        default=None)
    parser.add_argument('-reduce_chain', action='store_true')
    parser.add_argument('-reproduce', action='store_true')
    parser.add_argument('-deduplicate', action='store_true')
    argv = parser.parse_args()
    ctx.set_ctx(Context())

    init_mut_types([], ['simplifications', 'spacer_parameters', 'own'])
    if not argv.seed_file:
        if argv.reduce_chain:
            filenames = files.get_instance_names('output/bugs') \
                if not argv.bug_file else [argv.bug_file]
            for filename in filenames:
                print(filename)
                reduce(filename, argv.reduce_chain)
        else:
            filenames = files.get_filenames([argv.bug_file])
            if argv.reproduce:
                for filename in filenames:
                    redo_mutations(filename)
            else:
                deduplicate(filenames, argv.logfile)
    else:
        seed = parse_smt2_file(argv.seed_file,
                               ctx=ctx.current_ctx)
        mutant = parse_smt2_file(argv.bug_file,
                                 ctx=ctx.current_ctx)
        if equivalence_check(seed, mutant):
            print('Equivalent')
        else:
            assert False, 'The mutant is not equivalent to its seed'


if __name__ == '__main__':
    main()
