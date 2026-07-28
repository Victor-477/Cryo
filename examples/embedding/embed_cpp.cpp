/*
 * Example: Embedding Pyro VM in C++20 Application
 */
#include <iostream>
#include "../../../Burnout/embed/cpp/include/pyro.hpp"

int main() {
    std::cout << "=== LibPyro C++ Embedding Example ===" << std::endl;
    try {
        pyro::VM vm;
        pyro::Value res = vm.eval("int a = 15; int b = 27; print(a + b);");
        std::cout << "Eval executed cleanly" << std::endl;
    } catch (const std::exception& e) {
        std::cerr << "C++ Embedding Error: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
