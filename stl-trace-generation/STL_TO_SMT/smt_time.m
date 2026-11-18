% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_time(k, mT, fid)
    fprintf(fid, '\n\n ; TIMING CONSTRAINTS \n');
    for j = 1:k
        fprintf(fid, '(declare-fun t_%d () Real)\n', j);
        if j==1
           fprintf(fid, '(assert (= t_%d 0))\n', j);     
        else
            fprintf(fid, '(assert (< t_%d t_%d ))\n', j-1, j);
        end
    end
    fprintf(fid, '(assert (< t_%d %d))\n', k, mT);
%     
%     for j = 1:k
%         fprintf(fid, '(declare-fun t_%d () Real)\n', j);
%         if j==1
%            fprintf(fid, '(assert (= t_%d 0))\n', j);     
%         else
%             fprintf(fid, '(assert (<= t_%d t_%d ))\n', j-1, j);
%         end
%     end
%     fprintf(fid, '(assert (<= t_%d %d))\n', k, mT);
