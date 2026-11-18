% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function check_t_dt_constant(name1,name2,k,dt,fid)
    for j = 1:k
        if j < k
            fprintf(fid, '(assert (and true true ');
            for l = 1:k
                fprintf(fid, '(=> (and (<= (+ t_%d %f) t_%d) (< t_%d ( + t_%d %f) )) (= %s_%d %s_%d) )', j, dt, l, l, j+1, dt, name1, l, name2, j);    
                if l == k
                    fprintf(fid, '(=> (<= t_%d ( + t_%d %f)) (= %s_%d %s_%d))', k, j, dt, name1, k, name2,j);
                else
                    fprintf(fid, '(=> (and (<= t_%d ( + t_%d %f)) (< (+ t_%d %f) t_%d) ) (= %s_%d %s_%d))', l, j, dt, j, dt, l+1, name1, k, name2, j);
                end
            end
            fprintf(fid, ' ))\n');
        else
            fprintf(fid, '(assert (= %s_%d %s_%d))\n', name1, k, name2, k);
        end
    end
