/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks;

import it.units.malelab.learningstl.TreeNodes.*;
import it.units.malelab.jgea.representation.tree.Tree;

import java.util.*;
import java.util.function.Function;
import java.util.stream.Collectors;


public class STLFormulaMapper implements Function<Tree<String>, AbstractTreeNode> {

    @Override
    public AbstractTreeNode apply(Tree<String> root) {
        return parseSubTree(root, new ArrayList<>() {{ add(null); }});
    }

    public static AbstractTreeNode parseSubTree(Tree<String> currentNode, List<Tree<String>> ancestors) {
        List<Tree<String>> children = currentNode.childStream().collect(Collectors.toList());
        Tree<String> testChild = children.get(0);
        for (MonitorExpressions op : MonitorExpressions.values()) {
            if (op.toString().equals(testChild.content())) {
                ancestors.add(currentNode);
                return createMonitor(op, getSiblings(testChild, ancestors), ancestors);
            }
        }
        ancestors.add(currentNode);
        return parseSubTree(testChild, ancestors);
    }

    private static List<Tree<String>> getSiblings(Tree<String> node, List<Tree<String>> ancestors) {
        Tree<String> parent = ancestors.get(ancestors.size() - 1);
        if (parent == null) {
            return Collections.emptyList();
        }
        List<Tree<String>> res = parent.childStream().collect(Collectors.toList());
        res.remove(node);
        if (res.isEmpty()) {
            return getSiblings(parent, ancestors.stream().filter(x -> x != parent).collect(Collectors.toList()));
        }
        return res;
    }

    private static AbstractTreeNode createMonitor(MonitorExpressions op, List<Tree<String>> siblings, List<Tree<String>> ancestors) {
        if (op == MonitorExpressions.PROP) {
            return new NumericTreeNode(siblings);
        }
        if (op == MonitorExpressions.BOOL) {
            return new BoolTreeNode(siblings);
        }
        if (op == MonitorExpressions.NOT) {
            return new NotTreeNode(siblings, ancestors);
        }
        if (op == MonitorExpressions.AND) {
            return new AndTreeNode(siblings, ancestors);
        }
        if (op == MonitorExpressions.OR) {
            return new OrTreeNode(siblings, ancestors);
        }
        if (op == MonitorExpressions.IMPLIES) {
            return new ImpliesTreeNode(siblings, ancestors);
        }
        if (op == MonitorExpressions.UNTIL || op == MonitorExpressions.SINCE) {
            return new BinaryTemporalTreeNode(op, siblings, op.toString(), ancestors);
        }
        return new UnaryTemporalTreeNode(op, siblings, op.toString(), ancestors);
    }

}
