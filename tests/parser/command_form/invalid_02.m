% (c) Copyright 2019 Florian Schanda

function invalid_02()
    kitten = 12;

    foo(kitten); % 12
    foo = kitten; % semantic error (redef of foo after use)
end
