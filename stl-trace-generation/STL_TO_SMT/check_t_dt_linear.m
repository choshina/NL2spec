% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function check_t_dt_linear(name1, name2, k, dt, fid)
    for j = 1:k
        if j < k
            %fprintf(fid, '(assert (and true true ');
            for l = 1:k
                % Value at every point inside the interval t_j + dt and
                % t_{j+1} + dt in name1 signal is as expected according to
                % the name2 signal in the interval t_j to t_{j+1}
                fprintf(fid, '(assert (=> (and (<= (+ t_%d %f) t_%d) (< t_%d ( + t_%d %f) )) (= (* %s_%d (- t_%d t_%d)) (+ (* %s_%d (- t_%d t_%d)) (* (- %s_%d_end %s_%d) (- t_%d (+ t_%d %f))))) ))', j, dt, l, l, j+1, dt, name1, l, j+1, j, name2, j, j+1, j, name2, j, name2, j, l, j, dt);    
                if l > 1
                    fprintf(fid, '(assert (=> (and (<= (+ t_%d %f) t_%d) (< t_%d ( + t_%d %f) )) (= (* %s_%d_end (- t_%d t_%d)) (+ (* %s_%d (- t_%d t_%d)) (* (- %s_%d_end %s_%d) (- t_%d (+ t_%d %f))))) ))', j, dt, l, l, j+1, dt, name1, l-1, j+1, j, name2, j, j+1, j, name2, j, name2, j, l, j, dt);    
                end
                if l < k
                    fprintf(fid, '(assert (=> (and (<= t_%d (+ t_%d %f)) (< (+ t_%d %f) t_%d)) (= (* %s_%d (- t_%d t_%d)) (+ (* %s_%d (- t_%d t_%d)) (* (- %s_%d %s_%d) (- (+ t_%d %f) t_%d))))))', l, j, dt, j, dt, l+1, name2, j, l+1, l, name1, l, l+1, l, name1, l+1, name1, l, j, dt, l);
                    fprintf(fid, '(assert (=> (and (<= t_%d (+ t_%d %f)) (< (+ t_%d %f) t_%d)) (= (* %s_%d_end (- t_%d t_%d)) (+ (* %s_%d (- t_%d t_%d)) (* (- %s_%d %s_%d) (- (+ t_%d %f) t_%d))))))', l, j+1, dt, j+1, dt, l+1, name2, j, l+1, l, name1, l, l+1, l, name1, l+1, name1, l, j, dt, l);
                end
                if l == k
                    fprintf(fid, '(assert (=> (< t_%d ( + t_%d %f)) (and (= %s_%d %s_%d) (= %s_%d %s_%d_end)) ))', k, j+1, dt, name1, k, name2, j, name1, k, name2, j);
                end    
            end
            %fprintf(fid, ' ))\n');
        else
            fprintf(fid, '(assert (= %s_%d %s_%d))\n', name1, k, name2, k);
        end
    end
