% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function check_t_minus_dt_constant(name,k,dt,fid)
    fprintf(fid, '\n ; Check positive time shift constraints \n');
    name1 = strcat(name, '_t_m_dt');
    name2 = strcat(name, '_t');
    check_t_dt_constant(name1,name2,k,dt,fid);
