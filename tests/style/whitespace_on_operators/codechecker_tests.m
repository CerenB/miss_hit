% (c) Copyright 2020 Zenuity AB

%% These should be OK

var - 1 + 2

single(-1)

foo = ~var

var * 123

b = ~(var1 || ~var2 & var3) & var4 == false

((a * 3) <= 1) && ((single(1 + 1) - (1^3 + 4))/5 ~= (1/3))

a < -Eps

b > -another2Variable

[~, a] = foo(b)

[b, ~] = foo(b)

a < ~b

a < ...
-b

a = [-5, var, 4, -1, -4; ...
     6, 9, -2, varvar, -3]

a = {4 -1 4 -var -4; ...
     6 varvar -2 1 -3}

a = -1

a = single([-0.05 0 0 0]);

res = single([12.3 3.9 -16.0; ...
              12.9 12.0 -5.0]);

DoIt( ...
        -var1 > 2, single(-0.0625));

kitten = min(foo.bar.BAZ, ...
             max(single(0), ...
                 -foo.bar.wibble(potato, ...
                                 cat, single(0.001))));

var = 3e-4;

for i = 10:-1:-10
    [0 -1 -1 0]
end

a .* b ./ c
a.^b
a^b

import PackageName.*;

'STRING1-STRING2*STRING3'
'STRING1-STR""ING2*STRING3'
"STRING1-STRING2*STRING3"
"STRING1-STR''ING2*STRING3"

%% These should not be OK for a variety of reasons

var-1

single( -1)

single( - 1)

foo =~ var

var*123

var *( 123)

a ~=3

a.*b

a .^ b

a ^ b

import PackageName.* ;
