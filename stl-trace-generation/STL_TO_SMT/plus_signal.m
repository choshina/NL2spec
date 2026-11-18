% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function b = plus_signal(str, T)
    b = 0;
    n = size(T);
    for i=1:n(1)
        if strcmp(T{i,2}, 'predicate')
            B = strcmp(T{i,4}, str);
            for j = 1:numel(B)
                if B(j) == 1
                    b = 1;
                end
            end
        end
    end
