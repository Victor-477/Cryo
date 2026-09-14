/*
 * Example: Embedding Pyro VM in C# (.NET) Application
 */
using System;
using Pyro.Embedding;

class Program {
    static void Main() {
        Console.WriteLine("=== LibPyro C# (.NET) Embedding Example ===");
        try {
            var vm = new PyroVM();
            var res = vm.Eval("int a = 15; int b = 27; print(a + b);");
            Console.WriteLine("Eval executed cleanly");
        } catch (Exception ex) {
            Console.Error.WriteLine("C# Embedding Error: " + ex.Message);
            Environment.Exit(1);
        }
    }
}
