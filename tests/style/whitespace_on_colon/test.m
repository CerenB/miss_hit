% (c) Copyright 2019 Zenuity AB

%% ok

y = x(2, :);
y = x(3, 1:end);
y = x{:};
x = j:k;
x = j:i:k;
z = A(:);
z = A(j:k);

%% not ok

for i = 1 : 2
    z = A(m,:);
    z = A(:,n);
    x = j : i : k;
end
