% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_untimed_until(i, i1, i2, k, fid)
    fprintf(fid, '\n\n ; UNTIL \n');
    for l=1:k
        j = k - l + 1;
        fprintf(fid, '(declare-fun b_%d_%d () Bool)\n',i,j);
        if j == k
            fprintf(fid, '(assert (= b_%d_%d b_%d_%d))\n', i, j, i2, j);
        else
            fprintf(fid, '(assert (= b_%d_%d (or b_%d_%d (and b_%d_%d b_%d_%d))))\n', i, j, i2, j, i1, j, i, j+1);
        end
    end
