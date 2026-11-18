/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.TreeNodes;

import it.units.malelab.learningstl.BuildingBlocks.MonitorExpressions;
import it.units.malelab.learningstl.BuildingBlocks.STLFormulaMapper;
import eu.quanticol.moonlight.formula.DoubleDomain;
import eu.quanticol.moonlight.monitoring.temporal.TemporalMonitor;
import it.units.malelab.jgea.representation.tree.Tree;

import java.util.List;
import java.util.Objects;


public class UnaryTemporalTreeNode extends TemporalTreeNode {

    public UnaryTemporalTreeNode(MonitorExpressions op, List<Tree<String>> siblings, String message, List<Tree<String>> ancestors) {
        super(siblings, message);
        this.firstChild = STLFormulaMapper.parseSubTree(siblings.get(0), ancestors);
        if (op == MonitorExpressions.ONCE) {
            this.func = x -> TemporalMonitor.onceMonitor(this.firstChild.getOperator().apply(x), new DoubleDomain(), this.createInterval(x));
        }
        if (op == MonitorExpressions.EVENTUALLY) {
            this.func = x -> TemporalMonitor.eventuallyMonitor(this.firstChild.getOperator().apply(x), new DoubleDomain(), this.createInterval(x));
        }
        if (op == MonitorExpressions.HISTORICALLY) {
            this.func = x -> TemporalMonitor.historicallyMonitor(this.firstChild.getOperator().apply(x), new DoubleDomain(), this.createInterval(x));
        }
        if (op == MonitorExpressions.GLOBALLY) {
            this.func = x -> TemporalMonitor.globallyMonitor(this.firstChild.getOperator().apply(x), new DoubleDomain(), this.createInterval(x));
        }
    }

    @Override
    public int getNecessaryLength() {
        // An unbounded future operator is evaluated over the available finite
        // trace. It must not make every finite trace fail the length guard.
        return this.firstChild.getNecessaryLength()
                + (Double.isFinite(this.end) ? (int) Math.ceil(this.end) : 0);
    }

    @Override
    public void getVariablesAux(List<String[]> temp) {
        this.firstChild.getVariablesAux(temp);
    }

    @Override
    public int getNumBounds() {
        int ans = 0;
        ans += 2;
        ans += this.firstChild.getNumBounds();
        return ans;
    }

    @Override
    public int[] propagateParametersAux(double[] parameters, int[] idxs) {
        if (idxs[1] >= parameters.length && idxs[0] >= this.getNumBounds()) return idxs;
        // first parameter is start, second is offset with respect to start (to get interval end)
        double start = parameters[idxs[0]];
        double length = parameters[idxs[0] + 1];
        if (Double.isFinite(start) && Double.isFinite(length)) {
            this.setInterval(Math.max(0.0, start), Math.max(0.0, start) + Math.max(0.0, length));
        }
        idxs[0] += 2;
        return this.firstChild.propagateParametersAux(parameters, idxs);
    }

    @Override
    public boolean equals(Object o) {
        if (!super.equals(o)) {
            return false;
        }
        final AbstractTreeNode other = (AbstractTreeNode) o;
        return Objects.equals(this.firstChild, other.getFirstChild());
    }

    @Override
    public int hashCode() {
        int result = super.hashCode();
        result += 31 * result + (this.firstChild == null ? 0 : this.firstChild.hashCode());
        return result;
    }

}
