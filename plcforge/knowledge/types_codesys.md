# Types and CODESYS compilation
BOOL is distinct from INT. Use TRUE/FALSE, := assignment, = comparison, <> inequality,
AND/OR/NOT boolean operators, ELSIF, END_IF; END_CASE; END_FOR; and END_VAR.
Use explicit integer-to-real conversions before division when a real result is intended.
INT is 16-bit signed; DINT is 32-bit signed. Guard division by zero and limits.
Each .st file contains one complete PROGRAM or FUNCTION_BLOCK including the terminator.
The CODESYS textual editor splits declarations and implementation; the bridge removes
the POU terminator when populating the implementation. Put source files under src/.
An offline CODESYS compile requires a real executable, exact installed profile, scripting
support, and a standard .project with a device, application, and cyclic task. A POU must
be reachable from a task to ensure generation; the bridge creates a compile probe.
No lint or simulation output is a CODESYS compilation result. No hardware download occurs.
