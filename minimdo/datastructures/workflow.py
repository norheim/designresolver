from itertools import chain
from enum import Enum
from collections import OrderedDict
from graphutils import merge_edges, solver_children, end_components, Node, SOLVER, VAR, COMP, all_solvers
from utils import normalize_name

NodeTypesExtended = Enum('NodeTypesExtended', 'ENDCOMP')
NodeTypesExtended.__repr__ = lambda x: x.name
ENDCOMP = NodeTypesExtended.ENDCOMP
ExecutionTypes = Enum('ExecutionTypes', 'EXPL IMPL EQ NEQ OBJ SOLVE OPT')
ExecutionTypes.__repr__ = lambda x: x.name
EXPL, IMPL, EQ, NEQ, OBJ, SOLVE, OPT = ExecutionTypes.EXPL,ExecutionTypes.IMPL,ExecutionTypes.EQ,ExecutionTypes.NEQ,ExecutionTypes.OBJ,ExecutionTypes.SOLVE,ExecutionTypes.OPT

def namefromid(nodetyperepr):
    def nameingfunction(eltids, elttype, isiter=False):
        if isiter:
            return tuple(nodetyperepr[elttype].format(eltid) for eltid in eltids)
        else:
            return nodetyperepr[elttype].format(eltids)
    return nameingfunction

def path(Stree, s, visited=None):
    visited = visited if visited else set()
    out = []
    if s in chain(Stree.values(),Stree.keys()):
        q = {s}  
    else:
        q = set()
        out = [s] if s not in visited else [] # we should at least return the parent node
    while q:
        s = q.pop()
        if s not in visited:
            out.append(s)
            if s in Stree:
                q.add(Stree[s])
        visited.add(s)
    return out

def get_f(f, edges):
    #TODO: ensure that elt.inputs is of same type as Ein[comp], eg. str
    f_lookup = {(frozenset(elt.inputs),elt.component,frozenset(elt.outputs)): elt for elt in f}
    Ein = merge_edges(edges[0], edges[2])
    Eout = edges[1]
    def lookup(comp):
        key = (frozenset(Ein[comp]),comp,frozenset(Eout[comp]))
        return f_lookup[key] 
    return lookup

def addexpcomp_args(lookup_f, parent_solver_node, key, debug=False):
    component = lookup_f(key)
    input_names = [str(Node(inputvar,VAR)) for inputvar in component.inputs]
    output_names = [str(Node(outputvar, VAR)) for outputvar in component.outputs]
    args = (EXPL, str(parent_solver_node), str(Node(key, COMP)), input_names, output_names, component.evaldict, component.graddict, debug)
    return args

def implicit_comp_name(comps):
    return "res_{}".format('_'.join(map(lambda x: normalize_name(str(x)),comps)))

def addimpcomp_args(lookup_f, parent_solver_node, fends, output_names):
    impl_components = []
    comps = []
    for idx, comp_node in enumerate(fends):
        component = lookup_f(comp_node)
        input_names = [str(Node(inputvar,VAR)) for inputvar in component.inputs]
        output_name = [str(Node(output_names[idx], VAR))] #TODO: THIS IS VERY HACKY BUT WILL WORK FOR NOW
        impl_components.append((input_names, output_name, component.evaldict, component.graddict, 1.))
        comps.append(Node(comp_node, COMP))
    args = (str(parent_solver_node), implicit_comp_name(comps), impl_components)
    return args

def default_solver_options(tree, solvers_options=None):
    solvers_options = dict(solvers_options) if solvers_options else dict()
    allsolvers = all_solvers(tree)
    Vtree = tree[2]
    for solver in allsolvers:
        if solver not in solvers_options:
            solvers_options[solver] = dict()
        if 'type' not in solvers_options[solver]:
            solvers_options[solver]['type']=SOLVE
        solvers_options[solver]['designvars'] = tuple(solver_children(Vtree, solver))
    return solvers_options

def handle_component(allendcomps, queue, component, parent, endcompqueue):
    remainingallendcomps = len([elt for elt in solver_children(queue, parent) if elt in allendcomps])
    lastendcomp = component in allendcomps and remainingallendcomps==0
    if lastendcomp:
            return (ENDCOMP, endcompqueue+[component], parent), []
    elif component not in allendcomps: 
           return (COMP, component, parent), endcompqueue
    else:
        return None, endcompqueue+[component]

def order_from_tree(Ftree, Stree, Eout):
    visited = set()
    sequence = []
    queue = OrderedDict(Ftree)
    allendcomps = end_components(Eout)
    endcompqueue = []
    while queue:
        component, parent = queue.popitem(last=False)
        ancestors = path(Stree, parent, visited)
        reverse_ancestors = ancestors[::-1]
        visited = visited.union(reverse_ancestors)
        sequence += [(SOLVER, solver, Stree.get(solver,None)) for solver in reverse_ancestors]
        sequence_item, endcompqueue = handle_component(allendcomps, queue, component, parent, endcompqueue)
        if sequence_item:
            sequence.append(sequence_item)
    return sequence

def mdao_workflow(sequence, solvers_options, comp_options=None, var_options=None):
    comp_options = comp_options if comp_options else dict()
    workflow = []
    for elt_type, content, parent in sequence:
        if elt_type == SOLVER:
            solver_options = solvers_options[content]
            solver_type = solver_options['type']
            workflow.append((solver_type, content, parent, {key:val for key,val in solver_options.items() if key != 'type'}, var_options))
        elif elt_type == COMP:
            workflow.append((EXPL, content, parent))
        elif elt_type == ENDCOMP:
            if solvers_options[parent]['type'] == OPT:
                workflow += [(comp_options.get(comp, EQ), comp, parent) for comp in content]
            else:
                designvars = solvers_options[parent]['designvars']
                workflow.append((IMPL, content, designvars, parent))
    return workflow 

def generate_workflow(lookup_f, edges, tree, solvers_options=None, comp_options=None, debug=False):
    solvers_options = default_solver_options(solvers_options)
    Fend = end_components(edges[1])
    Ftree, Stree, Vtree = tree
    visited = set()
    workflow = []
    Ftree_copy = OrderedDict(Ftree)
    while Ftree_copy:
        key, parentsolver = Ftree_copy.popitem(last=False)
        parent_solver_node = Node(parentsolver,SOLVER)
        solverpath = path(Stree, parentsolver, visited)
        solverpath_rev = solverpath[::-1]
        visited = visited.union(solverpath)
        f = [elt for elt in solver_children(Ftree_copy, parentsolver) if elt in Fend]
        for solver_idx in solverpath_rev:
            parent_solver = Stree.get(solver_idx, None)
            parent_solver_name = str(Node(parent_solver,SOLVER)) if parent_solver else None
            solver_options = solvers_options[solver_idx] 
            solver_type = solver_options['type']
            workflow.append((solver_type, parent_solver_name, str(Node(solver_idx, SOLVER)), solver_options))
        if key in Fend and not f: #the last one
            fends = [elt for elt in solver_children(Ftree, parentsolver) if elt in Fend]
            if not solvers_options[parentsolver]['type'] == OPT:
                args = addimpcomp_args(lookup_f, parent_solver_node, fends, list(solver_children(Vtree, parentsolver)))
                workflow.append((IMPL, *args))
            else:
                for fend in fends:
                    function_role = comp_options[fend]
                    args = (parent_solver_node, function_role, lookup_f(fend))
                    workflow.append((function_role, *args)) #this would add them as design variable
        elif key not in Fend: 
            args = addexpcomp_args(lookup_f, parent_solver_node, key, debug)
            workflow.append(args)
    return workflow