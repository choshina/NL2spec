% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function [outputstr] = convert_brackets_to_underscores(inpstr)
% CONVERT_BRACKETS_TO_UNDERSCORES(INPSTR) takes a string representeing a 
% predicate that is based on some collection of signals and returns an
% output string that replaces bracketed epressions with underscores. For
% example, if a signal is represented as x[t], the signal name that is
% returned will be x_t. Also, if plus (+) or minus (-) are present in the
% bracketed expression, they will be replaced with either '_p_' or '_m_',
% respectively.
%
% Example usage:
%
% outputstr = convert_brackets_to_underscores('fqa_qafc_rq[t+dt]-fqa_qafc_rq[t]>.4+x[t]')
%
% J. Kapinski
% 6-2017
%

% Convert [t] variants to _t
outputstr = regexprep(inpstr,'\[\s*t\s*\]','_t');

% Convert [t+dt] variants to _t_p_dt
outputstr = regexprep(outputstr,'\[\s*t\s*\+\s*dt\s*\]|\[\s*dt\s*\+\s*t\s*\]','_t_p_dt');

% Convert [t-dt] variants to _t_m_dt
outputstr = regexprep(outputstr,'\[\s*t\s*-\s*dt\s*\]|\[\s*-\s*dt\s*\+\s*t\s*\]','_t_m_dt');

end
