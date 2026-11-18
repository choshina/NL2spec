% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_predicate(i, k, strstl, signal_pref, fid)
    fprintf(fid, '\n\n ; PREDICATES \n');
    if signal_pref == 0
        for j = 1:k
            fprintf(fid, '(declare-fun b_%d_%d () Bool)\n',i,j);
            str = pred_add_index(strstl, j);
            fprintf(fid, '(assert (= b_%d_%d %s ))\n', i, j, str);
        end
    else
        for j = 1:k
            fprintf(fid, '(declare-fun b_%d_%d () Bool)\n',i,j);
            str = pred_add_index(strstl, j);
            fprintf(fid, '(assert (= b_%d_%d %s ))\n', i, j, str);
            if j < k 
                str = pred_add_index_end(strstl, j);
                fprintf(fid, '(assert (= b_%d_%d %s ))\n', i, j, str);
            end
        end   
    end
