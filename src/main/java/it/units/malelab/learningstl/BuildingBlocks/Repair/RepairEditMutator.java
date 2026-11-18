/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This semantic repair extension builds on BUSTLE-derived tree interfaces.
 * See ../../PROVENANCE.md and the repository-level THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair;

import it.units.malelab.jgea.representation.tree.Tree;
import it.units.malelab.learningstl.BuildingBlocks.Repair.localization.RepairEdit;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Random;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class RepairEditMutator {

    private static final String NUMBER = "-?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?";
    private static final Pattern FOCUS_ATOM = Pattern.compile(
            "([A-Za-z_]\\w*)\\s*(<=|>=|==|=|<|>)\\s*(" + NUMBER + ")"
    );

    private static final List<String> SEMANTIC_OPS = List.of(
            ".prop", ".bool", ".and", ".or", ".implies", ".not",
            ".globally", ".eventually", ".until"
    );

    // One comparator edit used to do the work of three: "<" -> ">=" moves the
    // boundary and turns the comparison around in a single step, so neither
    // mistake can be repaired on its own. Split into three maps with disjoint
    // domains; a map that does not contain the current comparator means the
    // operator does not apply to that node.
    private static final Map<String, String> DIRECTION = Map.of(
            "<", ">",
            ">", "<",
            "<=", ">=",
            ">=", "<="
    );
    // Strictness (< <-> <=) is not an edit: under the robustness semantics x > c and x >= c
    // have the same robustness, so it is the identity. Equality (== <-> !=) is not an edit
    // either: "!=" is absent from the grammar and the monitors.

    private final int maxDepth;
    private final String focusMode;
    private final List<FocusAtom> focusAtoms;

    public RepairEditMutator(int maxDepth) {
        this(maxDepth, "off", List.of());
    }

    public RepairEditMutator(int maxDepth, String focusMode, List<String> focusClassFormulas) {
        this.maxDepth = maxDepth;
        this.focusMode = Set.of("off", "prefer", "only").contains(focusMode)
                ? focusMode
                : "off";
        this.focusAtoms = parseFocusAtoms(focusClassFormulas);
    }

    private boolean exceedsMaxDepth(Tree<String> candidate) {
        // Repair edits are local and bounded by edit budget. A non-positive
        // value deliberately disables the legacy global AST-depth guard so
        // deep seed formulas can still receive SGRL-guided edits.
        return maxDepth > 0 && candidate.height() > maxDepth;
    }

    public List<Tree<String>> mutateMany(Tree<String> parent, Random random, int count, int budget) {
        if (count <= 0) {
            return List.of();
        }
        int editBudget = Math.max(1, budget);
        List<Tree<String>> out = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        int attempts = Math.max(100, count * 60 * editBudget);
        for (int attempt = 0; attempt < attempts && out.size() < count; attempt++) {
            Tree<String> candidate = Tree.copyOf(parent);
            boolean changed = false;
            Set<String> touched = new LinkedHashSet<>();
            for (int i = 0; i < editBudget; i++) {
                if (applyOneEdit(candidate, random, touched)) {
                    changed = true;
                }
            }
            if (!changed || exceedsMaxDepth(candidate)) {
                continue;
            }
            String key = candidate.toString();
            if (seen.add(key)) {
                out.add(candidate);
            }
        }
        return out;
    }

    public List<Tree<String>> enumerateDeterministicLocalEdits(Tree<String> parent) {
        List<Tree<String>> out = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        List<LocatedNode> originalNodes = collectLocatedNodes(parent);
        List<Integer> nodeIndexes = new ArrayList<>();
        for (int i = 0; i < originalNodes.size(); i++) {
            if (!"only".equals(focusMode) || isFocused(originalNodes.get(i))) {
                nodeIndexes.add(i);
            }
        }
        if ("prefer".equals(focusMode) && !focusAtoms.isEmpty()) {
            nodeIndexes.sort((a, b) -> Boolean.compare(
                    isFocused(originalNodes.get(b)),
                    isFocused(originalNodes.get(a))
            ));
        }
        List<IntervalNodes> intervals = collectIntervals(parent);
        // Special root-scope edit: the requirement must hold over the whole
        // available future, independently of the finite intervals already in
        // the seed formula.
        if (!originalNodes.isEmpty()) {
            addDeterministicVariant(parent, 0, seen, out, this::wrapGlobalInfinity);
        }
        // First enumerate the operator relabels that most often repair scope
        // mistakes.  If we interleave every edit at each node, a small repair
        // population can spend all slots on root-level add/remove/wrap edits
        // and never reach an inner G/F flip such as G[0,200] -> F[0,200].
        for (int nodeIndex : nodeIndexes) {
            final int index = nodeIndex;
            addDeterministicVariant(parent, index, seen, out, this::flipTemporal);
        }
        for (int nodeIndex : nodeIndexes) {
            final int index = nodeIndex;
            addDeterministicVariant(parent, index, seen, out, this::flipBoolean);
            addDeterministicVariant(parent, index, seen, out, this::flipDirection);
            addDeterministicVariant(parent, index, seen, out, target -> replacePredicate(parent, target, new Random(31L + index)));
            addDeterministicVariant(parent, index, seen, out, target ->
                    ".not".equals(semanticOperator(target.node))
                            ? removeNot(target)
                            : insertNot(target)
            );
            addDeterministicVariant(parent, index, seen, out, target -> hoistTemporal(target, 1));
            addDeterministicVariant(parent, index, seen, out, target -> hoistTemporal(target, 2));
            addDeterministicVariant(parent, index, seen, out, target -> sinkTemporal(target, 1));
            addDeterministicVariant(parent, index, seen, out, target -> sinkTemporal(target, 2));
            addDeterministicVariant(parent, index, seen, out, this::splitTemporalOverJunction);
            addDeterministicVariant(parent, index, seen, out, this::mergeTemporalUnderJunction);
            addDeterministicVariant(parent, index, seen, out, this::unwrapTemporal);
            for (IntervalNodes interval : intervals) {
                addDeterministicVariant(parent, index, seen, out, target ->
                        wrapTemporal(target, interval, ".globally")
                );
                addDeterministicVariant(parent, index, seen, out, target ->
                        wrapTemporal(target, interval, ".eventually")
                );
            }
        }
        return out;
    }

    /** Enumerate one-step edits with stable semantic node paths for SGRL probes. */
    public List<RepairEdit> enumerateApplicableEdits(Tree<String> parent) {
        List<RepairEdit> out = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        List<LocatedNode> nodes = collectLocatedNodes(parent);
        List<IntervalNodes> intervals = collectIntervals(parent);
        if (!nodes.isEmpty()) {
            addRepairEditVariant(parent, 0, "wrap_global_inf", seen, out, this::wrapGlobalInfinity);
        }
        for (int index = 0; index < nodes.size(); index++) {
            addRepairEditVariant(parent, index, "flip_temporal", seen, out, this::flipTemporal);
            addRepairEditVariant(parent, index, "flip_boolean", seen, out, this::flipBoolean);
            addRepairEditVariant(parent, index, "flip_direction", seen, out, this::flipDirection);
            final int nodeIndex = index;
            addRepairEditVariant(parent, index, "replace_predicate", seen, out,
                    target -> replacePredicate(parent, target, new Random(31L + nodeIndex)));
            addRepairEditVariant(parent, index, "toggle_not", seen, out, target ->
                    ".not".equals(semanticOperator(target.node)) ? removeNot(target) : insertNot(target));
            addRepairEditVariant(parent, index, "hoist_temporal_left", seen, out,
                    target -> hoistTemporal(target, 1));
            addRepairEditVariant(parent, index, "hoist_temporal_right", seen, out,
                    target -> hoistTemporal(target, 2));
            addRepairEditVariant(parent, index, "sink_temporal_left", seen, out,
                    target -> sinkTemporal(target, 1));
            addRepairEditVariant(parent, index, "sink_temporal_right", seen, out,
                    target -> sinkTemporal(target, 2));
            addRepairEditVariant(parent, index, "split_temporal", seen, out,
                    this::splitTemporalOverJunction);
            addRepairEditVariant(parent, index, "merge_temporal", seen, out,
                    this::mergeTemporalUnderJunction);
            addRepairEditVariant(parent, index, "unwrap_temporal", seen, out, this::unwrapTemporal);
            for (IntervalNodes interval : intervals) {
                addRepairEditVariant(parent, index, "wrap_globally", seen, out,
                        target -> wrapTemporal(target, interval, ".globally"));
                addRepairEditVariant(parent, index, "wrap_eventually", seen, out,
                        target -> wrapTemporal(target, interval, ".eventually"));
            }
        }
        return out;
    }

    public Tree<String> applyEdit(Tree<String> parent, RepairEdit edit) {
        return edit == null ? Tree.copyOf(parent) : edit.apply();
    }

    private void addRepairEditVariant(
            Tree<String> parent,
            int nodeIndex,
            String family,
            Set<String> seen,
            List<RepairEdit> out,
            TargetEdit edit
    ) {
        Tree<String> candidate = Tree.copyOf(parent);
        List<LocatedNode> candidateNodes = collectLocatedNodes(candidate);
        List<LocatedNode> parentNodes = collectLocatedNodes(parent);
        if (nodeIndex >= candidateNodes.size() || nodeIndex >= parentNodes.size()
                || !edit.apply(candidateNodes.get(nodeIndex)) || exceedsMaxDepth(candidate)) {
            return;
        }
        String key = candidate.toString();
        if (!key.equals(parent.toString()) && seen.add(key)) {
            LocatedNode source = parentNodes.get(nodeIndex);
            out.add(new RepairEdit(source.path, family, nodeType(source), candidate));
        }
    }

    private void addDeterministicVariant(
            Tree<String> parent,
            int nodeIndex,
            Set<String> seen,
            List<Tree<String>> out,
            TargetEdit edit
    ) {
        Tree<String> candidate = Tree.copyOf(parent);
        List<LocatedNode> nodes = collectLocatedNodes(candidate);
        if (nodeIndex >= nodes.size() || !edit.apply(nodes.get(nodeIndex))) {
            return;
        }
        String key = candidate.toString();
        if (!key.equals(parent.toString()) && seen.add(key)) {
            out.add(candidate);
        }
    }

    private boolean applyOneEdit(Tree<String> root, Random random, Set<String> touched) {
        EditCategory preferred = sampleCategory(random);
        List<EditCategory> categories = new ArrayList<>(List.of(EditCategory.values()));
        categories.remove(preferred);
        Collections.shuffle(categories, random);
        categories.add(0, preferred);
        for (EditCategory category : categories) {
            List<LocatedNode> nodes = orderedNodes(root, random);
            for (LocatedNode target : nodes) {
                String touchKey = Integer.toString(System.identityHashCode(target.node));
                if (touched.contains(touchKey)) {
                    continue;
                }
                if (applyCategory(root, target, category, random)) {
                    touched.add(touchKey);
                    return true;
                }
            }
        }
        return false;
    }

    private EditCategory sampleCategory(Random random) {
        double draw = random.nextDouble();
        if (draw < 0.25) {
            return EditCategory.TEMPORAL_OPERATOR;
        }
        if (draw < 0.50) {
            return EditCategory.BOOLEAN_POLARITY;
        }
        if (draw < 0.75) {
            return EditCategory.TEMPORAL_SCOPE;
        }
        return EditCategory.PREDICATE_TEMPLATE;
    }

    private boolean applyCategory(
            Tree<String> root,
            LocatedNode target,
            EditCategory category,
            Random random
    ) {
        return switch (category) {
            case TEMPORAL_OPERATOR -> flipTemporal(target);
            case BOOLEAN_POLARITY -> relabel(target, random);
            case TEMPORAL_SCOPE -> random.nextBoolean()
                    ? hoistTemporal(target, random) || splitTemporalOverJunction(target)
                    || sinkTemporal(target, random) || mergeTemporalUnderJunction(target)
                    : splitTemporalOverJunction(target) || sinkTemporal(target, random)
                    || mergeTemporalUnderJunction(target) || hoistTemporal(target, random)
                    || wrapTemporal(root, target, random) || unwrapTemporal(target);
            case PREDICATE_TEMPLATE -> random.nextBoolean()
                    ? replacePredicate(root, target, random) || sampleComparatorEdit(target, random)
                    : sampleComparatorEdit(target, random) || replacePredicate(root, target, random);
        };
    }

    private boolean flipTemporal(LocatedNode target) {
        String op = semanticOperator(target.node);
        if (".globally".equals(op)) {
            replaceOperator(target.node, ".eventually");
            return true;
        }
        if (".eventually".equals(op)) {
            replaceOperator(target.node, ".globally");
            return true;
        }
        return false;
    }

    private boolean hoistNegatedTemporalScope(LocatedNode target, int negatedTemporalIndex) {
        String booleanOp = semanticOperator(target.node);
        if (!".or".equals(booleanOp)
                || target.node.nChildren() < 3
                || target.wrapper == null
                || !isExpressionSymbol(target.wrapper.content())
                || (negatedTemporalIndex != 1 && negatedTemporalIndex != 2)) {
            return false;
        }

        int siblingIndex = negatedTemporalIndex == 1 ? 2 : 1;
        Tree<String> negatedWrapper = target.node.child(negatedTemporalIndex);
        Tree<String> notNode = semanticNode(negatedWrapper);
        if (!".not".equals(semanticOperator(notNode)) || notNode.nChildren() < 2) {
            return false;
        }

        Tree<String> temporalWrapper = notNode.child(1);
        Tree<String> temporal = semanticNode(temporalWrapper);
        if (!isUnaryTemporal(temporal) || temporal.nChildren() < 4) {
            return false;
        }

        Tree<String> temporalChild = Tree.copyOf(temporal.child(1));
        Tree<String> negatedTemporalChild = Tree.copyOf(temporal.child(1));
        replaceChildren(negatedTemporalChild, List.of(Tree.of(
                logicSymbolForExpression(negatedTemporalChild.content()),
                Tree.of(".not"),
                temporalChild
        )));

        Tree<String> sibling = Tree.copyOf(target.node.child(siblingIndex));
        Tree<String> innerWrapper = Tree.copyOf(temporal.child(1));
        Tree<String> left = negatedTemporalIndex == 1 ? negatedTemporalChild : sibling;
        Tree<String> right = negatedTemporalIndex == 1 ? sibling : negatedTemporalChild;
        replaceChildren(innerWrapper, List.of(Tree.of(
                logicSymbolForExpression(innerWrapper.content()),
                Tree.of(".or"),
                left,
                right
        )));

        replaceChildren(target.wrapper, List.of(Tree.of(
                pastSymbolForExpression(target.wrapper.content()),
                Tree.of(semanticOperator(temporal)),
                innerWrapper,
                Tree.copyOf(temporal.child(2)),
                Tree.copyOf(temporal.child(3))
        )));
        return true;
    }

    private boolean hoistTemporal(LocatedNode target, Random random) {
        String booleanOp = semanticOperator(target.node);
        if (!(".and".equals(booleanOp) || ".or".equals(booleanOp) || ".implies".equals(booleanOp))
                || target.node.nChildren() < 3
                || target.wrapper == null) {
            return false;
        }
        int temporalIndex = random.nextBoolean() ? 1 : 2;
        return hoistTemporal(target, temporalIndex)
                || hoistTemporal(target, temporalIndex == 1 ? 2 : 1);
    }

    private boolean hoistTemporal(LocatedNode target, int temporalIndex) {
        String booleanOp = semanticOperator(target.node);
        if (!(".and".equals(booleanOp) || ".or".equals(booleanOp) || ".implies".equals(booleanOp))
                || target.node.nChildren() < 3
                || target.wrapper == null
                || (temporalIndex != 1 && temporalIndex != 2)) {
            return false;
        }
        int siblingIndex = temporalIndex == 1 ? 2 : 1;
        Tree<String> temporalWrapper = target.node.child(temporalIndex);
        Tree<String> temporal = semanticNode(temporalWrapper);
        if (!isUnaryTemporal(temporal) || temporal.nChildren() < 4) {
            return hoistNegatedTemporalScope(target, temporalIndex);
        }

        Tree<String> innerWrapper = Tree.copyOf(temporal.child(1));
        Tree<String> temporalChild = Tree.copyOf(temporal.child(1));
        Tree<String> sibling = Tree.copyOf(target.node.child(siblingIndex));
        replaceChildren(innerWrapper, List.of(Tree.of(
                logicSymbolForExpression(innerWrapper.content()),
                Tree.of(booleanOp),
                temporalChild,
                sibling
        )));
        replaceChildren(target.wrapper, List.of(Tree.of(
                pastSymbolForExpression(target.wrapper.content()),
                Tree.of(semanticOperator(temporal)),
                innerWrapper,
                Tree.copyOf(temporal.child(2)),
                Tree.copyOf(temporal.child(3))
        )));
        return true;
    }

    private boolean sinkTemporal(LocatedNode target, Random random) {
        int wrappedIndex = random.nextBoolean() ? 1 : 2;
        return sinkTemporal(target, wrappedIndex);
    }

    private boolean sinkTemporal(LocatedNode target, int wrappedIndex) {
        if (!isUnaryTemporal(target.node) || target.node.nChildren() < 4 || target.wrapper == null) {
            return false;
        }
        Tree<String> innerWrapper = target.node.child(1);
        Tree<String> booleanNode = semanticNode(innerWrapper);
        String booleanOp = semanticOperator(booleanNode);
        if (!(".and".equals(booleanOp) || ".or".equals(booleanOp) || ".implies".equals(booleanOp))
                || booleanNode.nChildren() < 3) {
            return false;
        }
        if (wrappedIndex != 1 && wrappedIndex != 2) {
            return false;
        }
        int siblingIndex = wrappedIndex == 1 ? 2 : 1;
        String temporalOp = semanticOperator(target.node);
        boolean nonDistributiveCombo = (".globally".equals(temporalOp) && ".or".equals(booleanOp))
                || (".eventually".equals(temporalOp) && ".and".equals(booleanOp));
        boolean asymmetricSplit = hasTemporalOperator(booleanNode.child(wrappedIndex))
                != hasTemporalOperator(booleanNode.child(siblingIndex));
        // Implication scope is directional: sinking onto the antecedent or
        // consequent is itself a meaningful repair, even when both branches
        // have the same temporal shape.
        if (!".implies".equals(booleanOp) && !nonDistributiveCombo && !asymmetricSplit) {
            return false;
        }
        Tree<String> originalBranch = Tree.copyOf(booleanNode.child(wrappedIndex));
        Tree<String> wrappedBranch = Tree.copyOf(booleanNode.child(wrappedIndex));
        replaceChildren(wrappedBranch, List.of(Tree.of(
                pastSymbolForExpression(wrappedBranch.content()),
                Tree.of(temporalOp),
                originalBranch,
                Tree.copyOf(target.node.child(2)),
                Tree.copyOf(target.node.child(3))
        )));
        Tree<String> left = wrappedIndex == 1
                ? wrappedBranch
                : Tree.copyOf(booleanNode.child(siblingIndex));
        Tree<String> right = wrappedIndex == 2
                ? wrappedBranch
                : Tree.copyOf(booleanNode.child(siblingIndex));
        replaceChildren(target.wrapper, List.of(Tree.of(
                logicSymbolForExpression(target.wrapper.content()),
                Tree.of(booleanOp),
                left,
                right
        )));
        return true;
    }

    /**
     * Push a temporal operator onto <em>every</em> branch of the junction below it:
     * {@code OP_I[phi o psi] -> OP_I[phi] o OP_I[psi]}.
     *
     * <p>This is the total counterpart of {@link #sinkTemporal}, which wraps one
     * branch and leaves the sibling bare. G over AND and F over OR are
     * equivalence-preserving; the other two combinations are not, which is
     * precisely why they are worth searching.
     */
    private boolean splitTemporalOverJunction(LocatedNode target) {
        if (!isUnaryTemporal(target.node) || target.node.nChildren() < 4 || target.wrapper == null) {
            return false;
        }
        Tree<String> innerWrapper = target.node.child(1);
        Tree<String> booleanNode = semanticNode(innerWrapper);
        String booleanOp = semanticOperator(booleanNode);
        if (!(".and".equals(booleanOp) || ".or".equals(booleanOp)) || booleanNode.nChildren() < 3) {
            return false;
        }
        String temporalOp = semanticOperator(target.node);
        Tree<String> lowerBound = Tree.copyOf(target.node.child(2));
        Tree<String> upperBound = Tree.copyOf(target.node.child(3));

        List<Tree<String>> wrapped = new ArrayList<>();
        for (int branchIndex = 1; branchIndex <= 2; branchIndex++) {
            Tree<String> branch = Tree.copyOf(booleanNode.child(branchIndex));
            Tree<String> original = Tree.copyOf(booleanNode.child(branchIndex));
            replaceChildren(branch, List.of(Tree.of(
                    pastSymbolForExpression(branch.content()),
                    Tree.of(temporalOp),
                    original,
                    Tree.copyOf(lowerBound),
                    Tree.copyOf(upperBound)
            )));
            wrapped.add(branch);
        }
        replaceChildren(target.wrapper, List.of(Tree.of(
                logicSymbolForExpression(target.wrapper.content()),
                Tree.of(booleanOp),
                wrapped.get(0),
                wrapped.get(1)
        )));
        return true;
    }

    /**
     * The inverse of {@link #splitTemporalOverJunction}, so the dimension is
     * reachable in both directions: {@code OP_I[phi] o OP_I[psi] -> OP_I[phi o psi]}.
     *
     * <p>Every branch must be a temporal node of the same type over the same
     * interval; otherwise the merge would invent a scope that was never there.
     */
    private boolean mergeTemporalUnderJunction(LocatedNode target) {
        String booleanOp = semanticOperator(target.node);
        if (!(".and".equals(booleanOp) || ".or".equals(booleanOp))
                || target.node.nChildren() < 3
                || target.wrapper == null) {
            return false;
        }
        Tree<String> firstTemporal = null;
        List<Tree<String>> inner = new ArrayList<>();
        for (int branchIndex = 1; branchIndex <= 2; branchIndex++) {
            Tree<String> temporal = semanticNode(target.node.child(branchIndex));
            if (!isUnaryTemporal(temporal) || temporal.nChildren() < 4) {
                return false;
            }
            if (firstTemporal == null) {
                firstTemporal = temporal;
            } else if (!semanticOperator(temporal).equals(semanticOperator(firstTemporal))
                    || !sameInterval(temporal, firstTemporal)) {
                return false;
            }
            inner.add(Tree.copyOf(temporal.child(1)));
        }
        if (firstTemporal == null) {
            return false;
        }
        Tree<String> merged = Tree.copyOf(firstTemporal.child(1));
        replaceChildren(merged, List.of(Tree.of(
                logicSymbolForExpression(merged.content()),
                Tree.of(booleanOp),
                inner.get(0),
                inner.get(1)
        )));
        replaceChildren(target.wrapper, List.of(Tree.of(
                pastSymbolForExpression(target.wrapper.content()),
                Tree.of(semanticOperator(firstTemporal)),
                merged,
                Tree.copyOf(firstTemporal.child(2)),
                Tree.copyOf(firstTemporal.child(3))
        )));
        return true;
    }

    private boolean sameInterval(Tree<String> left, Tree<String> right) {
        return left.child(2).toString().equals(right.child(2).toString())
                && left.child(3).toString().equals(right.child(3).toString());
    }

    private boolean relabel(LocatedNode target, Random random) {
        String op = semanticOperator(target.node);
        if (".globally".equals(op) || ".eventually".equals(op)) {
            return flipTemporal(target);
        }
        if (".and".equals(op) || ".or".equals(op)) {
            return flipBoolean(target);
        }
        if (".not".equals(op)) {
            return removeNot(target);
        }
        return random.nextBoolean() && insertNot(target);
    }

    private boolean flipBoolean(LocatedNode target) {
        String op = semanticOperator(target.node);
        if (".and".equals(op)) {
            replaceOperator(target.node, ".or");
            return true;
        }
        if (".or".equals(op)) {
            replaceOperator(target.node, ".and");
            return true;
        }
        return false;
    }

    /**
     * Turn the comparison around without changing strictness: {@code < <-> >}.
     * This is the edit for a threshold compared on the wrong side.
     */
    private boolean flipDirection(LocatedNode target) {
        return applyComparatorMap(target, DIRECTION);
    }

    /** The only comparator edit: turn the comparison around. */
    private boolean sampleComparatorEdit(LocatedNode target, Random random) {
        return applyComparatorMap(target, DIRECTION);
    }

    private boolean applyComparatorMap(LocatedNode target, Map<String, String> map) {
        if (!".prop".equals(semanticOperator(target.node)) || target.node.nChildren() < 3) {
            return false;
        }
        Tree<String> comp = target.node.child(2);
        if (comp.nChildren() < 1) {
            return false;
        }
        // The three maps have disjoint domains, so "this operator does not apply
        // to this node" and "this node is not an atom" both land here.
        String replacement = map.get(comp.child(0).content());
        if (replacement == null) {
            return false;
        }
        comp.clearChildren();
        comp.addChild(Tree.of(replacement));
        return true;
    }

    /** Signal names are never changed: swapping an atom for another variable is outside the edit space (user decision 2026-11-03). */
    private static final boolean ALLOW_REPLACE_PREDICATE = false;

    private boolean replacePredicate(Tree<String> root, LocatedNode target, Random random) {
        if (!ALLOW_REPLACE_PREDICATE) {
            return false;
        }
        String targetOp = semanticOperator(target.node);
        if (!isAtomOperator(targetOp)) {
            return false;
        }
        List<LocatedNode> atoms = collectLocatedNodes(root).stream()
                .filter(node -> Objects.equals(targetOp, semanticOperator(node.node))
                        && node.node != target.node
                        && !node.node.toString().equals(target.node.toString()))
                .toList();
        if (atoms.isEmpty()) {
            return false;
        }
        Tree<String> originalConstant = target.node.nChildren() >= 4
                ? Tree.copyOf(target.node.child(3)) : null;
        Tree<String> replacement = atoms.get(random.nextInt(atoms.size())).node;
        copyInto(target.node, replacement);
        // replPred changes predicate structure but preserves the original threshold.
        if (originalConstant != null && target.node.nChildren() >= 4) {
            copyInto(target.node.child(3), originalConstant);
        }
        return true;
    }

    private boolean insertNot(LocatedNode target) {
        if (".not".equals(semanticOperator(target.node))
                || ".not".equals(semanticOperator(target.parentNode))
                || target.wrapper == null
                || !isExpressionSymbol(target.wrapper.content())) {
            return false;
        }
        Tree<String> original = Tree.copyOf(target.wrapper);
        replaceChildren(target.wrapper, List.of(Tree.of(
                logicSymbolForExpression(target.wrapper.content()),
                Tree.of(".not"),
                original
        )));
        return true;
    }

    private boolean removeNot(LocatedNode target) {
        if (!".not".equals(semanticOperator(target.node)) || target.node.nChildren() < 2 || target.wrapper == null) {
            return false;
        }
        copyInto(target.wrapper, target.node.child(1));
        return true;
    }

    private boolean wrapTemporal(Tree<String> root, LocatedNode target, Random random) {
        if (target.wrapper == null || !isExpressionSymbol(target.wrapper.content())) {
            return false;
        }
        List<IntervalNodes> intervals = collectIntervals(root);
        IntervalNodes interval = intervals.isEmpty()
                ? new IntervalNodes(literalNumber("<interval_symbol>", 0.0), literalNumber("<interval_symbol>", 1.0))
                : intervals.get(random.nextInt(intervals.size()));
        String temporalOp = random.nextBoolean() ? ".eventually" : ".globally";
        return wrapTemporal(target, interval, temporalOp);
    }

    /** Wrap exactly the complete formula in G[0,inf]. */
    private boolean wrapGlobalInfinity(LocatedNode target) {
        if (!"root".equals(target.path)
                || target.wrapper == null
                || !isExpressionSymbol(target.wrapper.content())) {
            return false;
        }
        String op = semanticOperator(target.node);
        if (".globally".equals(op)
                && target.node.nChildren() >= 4
                && isZeroLiteral(target.node.child(2))
                && isPositiveInfinityLiteral(target.node.child(3))) {
            return false;
        }
        Tree<String> original = Tree.copyOf(target.wrapper);
        replaceChildren(target.wrapper, List.of(Tree.of(
                pastSymbolForExpression(target.wrapper.content()),
                Tree.of(".globally"),
                original,
                literalNumber("<interval_symbol>", 0.0),
                literalInfinity("<interval_symbol>")
        )));
        return true;
    }

    private boolean wrapTemporal(LocatedNode target, IntervalNodes interval, String temporalOp) {
        if (target.wrapper == null
                || !isExpressionSymbol(target.wrapper.content())
                || !(".eventually".equals(temporalOp) || ".globally".equals(temporalOp))
                || hasTemporalOperator(target.node)
                || target.temporalAncestorCount > 0
                || temporalCount(target.root) >= 4
                || maxTemporalDepth(target.root) > 2) {
            return false;
        }
        Tree<String> original = Tree.copyOf(target.wrapper);
        replaceChildren(target.wrapper, List.of(Tree.of(
                pastSymbolForExpression(target.wrapper.content()),
                Tree.of(temporalOp),
                original,
                Tree.copyOf(interval.start),
                Tree.copyOf(interval.length)
        )));
        return true;
    }

    private boolean unwrapTemporal(LocatedNode target) {
        String op = semanticOperator(target.node);
        if (!((".eventually".equals(op) || ".globally".equals(op))) || target.node.nChildren() < 2 || target.wrapper == null) {
            return false;
        }
        copyInto(target.wrapper, target.node.child(1));
        return true;
    }

    /** |V_phi|: number of STL syntax nodes (operators and atoms) of the formula. */
    public int countFormulaNodes(Tree<String> root) {
        return collectLocatedNodes(root).size();
    }

    private List<LocatedNode> collectLocatedNodes(Tree<String> root) {
        List<LocatedNode> nodes = new ArrayList<>();
        collectLocatedNodes(root, null, null, root, 0, "root", nodes);
        return nodes;
    }

    private void collectLocatedNodes(
            Tree<String> current,
            Tree<String> expressionWrapper,
            Tree<String> parentSemantic,
            Tree<String> root,
            int temporalAncestorCount,
            String path,
            List<LocatedNode> out
    ) {
        Tree<String> wrapper = isExpressionSymbol(current.content()) ? current : expressionWrapper;
        Tree<String> semantic = semanticNode(current);
        if (semantic != null) {
            out.add(new LocatedNode(semantic, wrapper, parentSemantic, root, temporalAncestorCount, path));
            int childTemporalAncestorCount = temporalAncestorCount + (isTemporalOperator(semantic) ? 1 : 0);
            int semanticChild = 0;
            int semanticChildCount = 0;
            for (int i = 1; i < semantic.nChildren(); i++) {
                if (semanticNode(semantic.child(i)) != null) {
                    semanticChildCount++;
                }
            }
            for (int i = 1; i < semantic.nChildren(); i++) {
                boolean hasSemantic = semanticNode(semantic.child(i)) != null;
                String childPath = path;
                if (hasSemantic) {
                    if (semanticChildCount == 1) {
                        childPath += "/child";
                    } else if (semanticChild == 0) {
                        childPath += "/left";
                    } else if (semanticChild == 1) {
                        childPath += "/right";
                    } else {
                        childPath += "/child" + semanticChild;
                    }
                    semanticChild++;
                }
                collectLocatedNodes(
                        semantic.child(i),
                        isExpressionSymbol(semantic.child(i).content()) ? semantic.child(i) : wrapper,
                        semantic,
                        root,
                        childTemporalAncestorCount,
                        childPath,
                        out
                );
            }
            return;
        }
        for (int i = 0; i < current.nChildren(); i++) {
            collectLocatedNodes(current.child(i), wrapper, parentSemantic, root, temporalAncestorCount, path, out);
        }
    }

    private String nodeType(LocatedNode node) {
        String op = semanticOperator(node.node);
        return op == null ? "unknown" : op.replace(".", "").toUpperCase();
    }

    private List<IntervalNodes> collectIntervals(Tree<String> root) {
        List<IntervalNodes> intervals = new ArrayList<>();
        for (LocatedNode located : collectLocatedNodes(root)) {
            String op = semanticOperator(located.node);
            if ((".globally".equals(op) || ".eventually".equals(op)) && located.node.nChildren() >= 4) {
                intervals.add(new IntervalNodes(Tree.copyOf(located.node.child(2)), Tree.copyOf(located.node.child(3))));
            } else if (".until".equals(op) && located.node.nChildren() >= 4) {
                intervals.add(new IntervalNodes(Tree.copyOf(located.node.child(2)), Tree.copyOf(located.node.child(3))));
            }
        }
        return intervals;
    }

    private Tree<String> semanticNode(Tree<String> current) {
        if (semanticOperator(current) != null) {
            return current;
        }
        if (current.nChildren() == 1) {
            return semanticNode(current.child(0));
        }
        return null;
    }

    private String semanticOperator(Tree<String> node) {
        if (node == null || node.nChildren() == 0) {
            return null;
        }
        String content = node.child(0).content();
        return SEMANTIC_OPS.contains(content) ? content : null;
    }

    private boolean isUnaryTemporal(Tree<String> node) {
        String op = semanticOperator(node);
        return ".globally".equals(op) || ".eventually".equals(op);
    }

    private boolean isTemporalOperator(Tree<String> node) {
        String op = semanticOperator(node);
        return ".globally".equals(op) || ".eventually".equals(op) || ".until".equals(op);
    }

    private boolean isAtomOperator(String op) {
        return ".prop".equals(op) || ".bool".equals(op);
    }

    private boolean hasTemporalOperator(Tree<String> node) {
        if (node == null) {
            return false;
        }
        Tree<String> semantic = semanticNode(node);
        if (semantic != null) {
            if (isTemporalOperator(semantic)) {
                return true;
            }
            for (int i = 1; i < semantic.nChildren(); i++) {
                if (hasTemporalOperator(semantic.child(i))) {
                    return true;
                }
            }
            return false;
        }
        for (int i = 0; i < node.nChildren(); i++) {
            if (hasTemporalOperator(node.child(i))) {
                return true;
            }
        }
        return false;
    }

    private int temporalCount(Tree<String> node) {
        if (node == null) {
            return 0;
        }
        Tree<String> semantic = semanticNode(node);
        if (semantic != null) {
            int count = isTemporalOperator(semantic) ? 1 : 0;
            for (int i = 1; i < semantic.nChildren(); i++) {
                count += temporalCount(semantic.child(i));
            }
            return count;
        }
        int count = 0;
        for (int i = 0; i < node.nChildren(); i++) {
            count += temporalCount(node.child(i));
        }
        return count;
    }

    private int maxTemporalDepth(Tree<String> node) {
        return maxTemporalDepth(node, 0);
    }

    private int maxTemporalDepth(Tree<String> node, int currentDepth) {
        if (node == null) {
            return currentDepth;
        }
        Tree<String> semantic = semanticNode(node);
        if (semantic != null) {
            int nextDepth = isTemporalOperator(semantic) ? currentDepth + 1 : currentDepth;
            int max = nextDepth;
            for (int i = 1; i < semantic.nChildren(); i++) {
                max = Math.max(max, maxTemporalDepth(semantic.child(i), nextDepth));
            }
            return max;
        }
        int max = currentDepth;
        for (int i = 0; i < node.nChildren(); i++) {
            max = Math.max(max, maxTemporalDepth(node.child(i), currentDepth));
        }
        return max;
    }

    private Double readLiteral(Tree<String> node) {
        if (node == null) {
            return null;
        }
        try {
            return Double.parseDouble(node.content());
        } catch (NumberFormatException ignored) {
            // Numeric grammar nodes store the actual value in a descendant.
        }
        for (int i = 0; i < node.nChildren(); i++) {
            Double value = readLiteral(node.child(i));
            if (value != null) {
                return value;
            }
        }
        return null;
    }

    private List<FocusAtom> parseFocusAtoms(List<String> formulas) {
        Set<FocusAtom> atoms = new LinkedHashSet<>();
        for (String formula : formulas == null ? List.<String>of() : formulas) {
            Matcher matcher = FOCUS_ATOM.matcher(formula);
            while (matcher.find()) {
                String comparator = matcher.group(2).equals("=") ? "==" : matcher.group(2);
                atoms.add(new FocusAtom(
                        matcher.group(1),
                        comparator,
                        Double.parseDouble(matcher.group(3))
                ));
            }
        }
        return new ArrayList<>(atoms);
    }

    private List<LocatedNode> orderedNodes(Tree<String> root, Random random) {
        List<LocatedNode> all = collectLocatedNodes(root);
        if (focusAtoms.isEmpty() || "off".equals(focusMode)) {
            Collections.shuffle(all, random);
            return all;
        }
        List<LocatedNode> focused = new ArrayList<>();
        List<LocatedNode> other = new ArrayList<>();
        for (LocatedNode node : all) {
            (isFocused(node) ? focused : other).add(node);
        }
        Collections.shuffle(focused, random);
        if ("only".equals(focusMode)) {
            return focused;
        }
        Collections.shuffle(other, random);
        focused.addAll(other);
        return focused;
    }

    private boolean isFocused(LocatedNode target) {
        if (focusAtoms.isEmpty() || target == null || target.wrapper == null) {
            return focusAtoms.isEmpty();
        }
        for (LocatedNode descendant : collectLocatedNodes(target.wrapper)) {
            Tree<String> node = descendant.node;
            if (!".prop".equals(semanticOperator(node)) || node.nChildren() < 4) {
                continue;
            }
            String variable = node.child(1).nChildren() > 0
                    ? node.child(1).child(0).content()
                    : "";
            String comparator = node.child(2).nChildren() > 0
                    ? node.child(2).child(0).content()
                    : "";
            Double threshold = readLiteral(node.child(3));
            for (FocusAtom atom : focusAtoms) {
                if (threshold != null
                        && atom.variable.equals(variable)
                        && atom.comparator.equals(comparator)
                        && Math.abs(atom.threshold - threshold) <= 1.0E-9) {
                    return true;
                }
            }
        }
        return false;
    }

    private boolean isExpressionSymbol(String symbol) {
        return symbol != null && symbol.startsWith("<expr_");
    }

    private String logicSymbolForExpression(String exprSymbol) {
        if (exprSymbol.equals("<expr_first_level>")) {
            return "<logic_first_level>";
        }
        if (exprSymbol.equals("<expr_second_level>")) {
            return "<logic_second_level>";
        }
        return "<logic_third_level_onwards>";
    }

    private String pastSymbolForExpression(String exprSymbol) {
        if (exprSymbol.equals("<expr_first_level>")) {
            return "<past_first_level>";
        }
        if (exprSymbol.equals("<expr_second_level>")) {
            return "<past_second_level>";
        }
        return "<past_third_level_onwards>";
    }

    private Tree<String> literalNumber(String symbol, double value) {
        String text = Math.rint(value) == value ? Integer.toString((int) value) : Double.toString(value);
        return Tree.of(symbol, Tree.of("literal_num"), Tree.of(text));
    }

    private Tree<String> literalInfinity(String symbol) {
        return Tree.of(symbol, Tree.of("literal_num"), Tree.of("inf"));
    }

    private boolean isZeroLiteral(Tree<String> node) {
        Double value = readLiteral(node);
        return value != null && Math.abs(value) <= 1.0E-12;
    }

    private boolean isPositiveInfinityLiteral(Tree<String> node) {
        if (node == null) {
            return false;
        }
        if ("inf".equalsIgnoreCase(node.content()) || "infinity".equalsIgnoreCase(node.content())) {
            return true;
        }
        for (int i = 0; i < node.nChildren(); i++) {
            if (isPositiveInfinityLiteral(node.child(i))) {
                return true;
            }
        }
        return false;
    }

    private void copyInto(Tree<String> target, Tree<String> source) {
        replaceChildren(target, copyChildren(source));
    }

    private void replaceOperator(Tree<String> target, String op) {
        List<Tree<String>> children = copyChildren(target);
        children.set(0, Tree.of(op));
        replaceChildren(target, children);
    }

    private List<Tree<String>> copyChildren(Tree<String> node) {
        List<Tree<String>> children = new ArrayList<>();
        for (int i = 0; i < node.nChildren(); i++) {
            children.add(Tree.copyOf(node.child(i)));
        }
        return children;
    }

    private void replaceChildren(Tree<String> target, List<Tree<String>> children) {
        target.clearChildren();
        for (Tree<String> child : children) {
            target.addChild(child);
        }
    }

    private enum EditCategory {
        TEMPORAL_OPERATOR,
        BOOLEAN_POLARITY,
        TEMPORAL_SCOPE,
        PREDICATE_TEMPLATE
    }

    @FunctionalInterface
    private interface TargetEdit {
        boolean apply(LocatedNode target);
    }

    private static class LocatedNode {
        final Tree<String> node;
        final Tree<String> wrapper;
        final Tree<String> parentNode;
        final Tree<String> root;
        final int temporalAncestorCount;
        final String path;

        LocatedNode(
                Tree<String> node,
                Tree<String> wrapper,
                Tree<String> parentNode,
                Tree<String> root,
                int temporalAncestorCount,
                String path
        ) {
            this.node = node;
            this.wrapper = wrapper;
            this.parentNode = parentNode;
            this.root = root;
            this.temporalAncestorCount = temporalAncestorCount;
            this.path = path;
        }
    }

    private static class IntervalNodes {
        final Tree<String> start;
        final Tree<String> length;

        IntervalNodes(Tree<String> start, Tree<String> length) {
            this.start = start;
            this.length = length;
        }
    }

    private static class FocusAtom {
        final String variable;
        final String comparator;
        final double threshold;

        FocusAtom(String variable, String comparator, double threshold) {
            this.variable = variable;
            this.comparator = comparator;
            this.threshold = threshold;
        }

        @Override
        public boolean equals(Object other) {
            if (this == other) {
                return true;
            }
            if (!(other instanceof FocusAtom)) {
                return false;
            }
            FocusAtom atom = (FocusAtom) other;
            return variable.equals(atom.variable)
                    && comparator.equals(atom.comparator)
                    && Double.compare(threshold, atom.threshold) == 0;
        }

        @Override
        public int hashCode() {
            return Objects.hash(variable, comparator, threshold);
        }
    }
}
