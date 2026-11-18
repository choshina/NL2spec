/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.TreeNodes;

import eu.quanticol.moonlight.monitoring.temporal.TemporalMonitor;
import it.units.malelab.jgea.representation.tree.Tree;

import java.util.List;

public class BoolTreeNode extends AbstractTreeNode {

    private final String var;

    public BoolTreeNode(List<Tree<String>> siblings) {
        super();
        this.var = siblings.get(0).child(0).content();
        this.func = x -> TemporalMonitor.atomicMonitor(y -> {
            Double value = y.get(this.var);
            return value != null && value >= 1.0 ? 1.0 : -1.0;
        });
    }

    @Override
    public int getNecessaryLength() {
        return 0;
    }

    @Override
    public String getSymbol() {
        return this.var;
    }

    @Override
    public void getVariablesAux(List<String[]> temp) {
        temp.add(new String[] {this.var, "bool", "null"});
    }

    @Override
    public int getNumBounds() {
        return 0;
    }

    @Override
    public int[] propagateParametersAux(double[] parameters, int[] idxs) {
        return idxs;
    }
}
