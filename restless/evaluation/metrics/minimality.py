"""Ordered Zhang-Shasha tree-edit distance and STL atom preservation."""
from __future__ import annotations
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
from restless.common.stl_epoch.ast_nodes import walk_preorder
from restless.common.stl_epoch.parser import parse_stl

def children(node):
    if node.kind in ("not","always","eventually"):return [node.child]
    if node.kind in ("and","or","implies","until"):return [node.left,node.right]
    return []
def label(node):
    if node.kind=="atom":return f"atom:{node.var}:{node.cmp}:{float(node.value):g}"
    if node.kind=="bool":return f"bool:{node.var}"
    if node.kind in ("always","eventually","until"):return f"{node.kind}:{float(node.a):g}:{float(node.b):g}"
    return node.kind
def _postorder(root):
    nodes=[];lmd=[]
    def visit(node):
        child_ids=[visit(c) for c in children(node)];idx=len(nodes);nodes.append(node);lmd.append(lmd[child_ids[0]] if child_ids else idx);return idx
    visit(root);return nodes,lmd
def tree_edit_distance(left,right):
    a,la=_postorder(parse_stl(left) if isinstance(left,str) else left);b,lb=_postorder(parse_stl(right) if isinstance(right,str) else right)
    key_a=[i for i in range(len(a)) if i==max(j for j,x in enumerate(la) if x==la[i])]
    key_b=[i for i in range(len(b)) if i==max(j for j,x in enumerate(lb) if x==lb[i])]
    td=[[0]*len(b) for _ in a]
    for i in key_a:
        for j in key_b:
            rows=i-la[i]+2;cols=j-lb[j]+2;fd=[[0]*cols for _ in range(rows)]
            for x in range(1,rows):fd[x][0]=fd[x-1][0]+1
            for y in range(1,cols):fd[0][y]=fd[0][y-1]+1
            for x in range(1,rows):
                ai=la[i]+x-1
                for y in range(1,cols):
                    bj=lb[j]+y-1
                    if la[ai]==la[i] and lb[bj]==lb[j]:
                        fd[x][y]=min(fd[x-1][y]+1,fd[x][y-1]+1,fd[x-1][y-1]+(label(a[ai])!=label(b[bj])));td[ai][bj]=fd[x][y]
                    else:
                        fd[x][y]=min(fd[x-1][y]+1,fd[x][y-1]+1,fd[la[ai]-la[i]][lb[bj]-lb[j]]+td[ai][bj])
    return td[-1][-1]
def atom_preservation(initial,repaired):
    def atoms(text):return {label(n) for n,_ in walk_preorder(parse_stl(text)) if n.kind in ("atom","bool")}
    before=atoms(initial);after=atoms(repaired);return len(before&after)/len(before) if before else 1.0
