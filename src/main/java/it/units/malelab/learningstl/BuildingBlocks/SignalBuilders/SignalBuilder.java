/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.SignalBuilders;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;


public interface SignalBuilder<T> {

    default BufferedReader createReaderFromFile(String fileName) throws IOException {
        Path candidate = Paths.get(fileName);
        Path path = candidate.isAbsolute() ? candidate : Paths.get(".", fileName);
        InputStream in = Files.newInputStream(path);
        return new BufferedReader(new InputStreamReader(in));
    }

    List<T> parseSignals(String fileName) throws IOException;

    Map<String, double[]> getVarsBounds();

    double[] getTemporalBounds();

    default double[] readVectorFromFile(String s) throws IOException {return null;}
}
