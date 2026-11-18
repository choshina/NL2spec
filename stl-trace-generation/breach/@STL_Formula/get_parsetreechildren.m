function [phiout, phileft, phiright] = get_parsetreechildren(phi)
 % GET_PARSETREECHILDREN(PHI) returns the immediate children of the parse tree
 % for the STL formula (PHI).
 %
 % Usage Example:
 %
 % [phi,phi1,phi2] = get_parsetreechildren(myformula)
 %
 phiout = phi.phi;
 phileft = phi.phi1;
 phiright = phi.phi2;
end