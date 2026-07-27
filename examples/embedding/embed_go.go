/*
 * Example: Embedding Pyro VM in Go Application
 */
package main

import (
	"fmt"
	"github.com/pyro-cryo/pyro"
)

func main() {
	fmt.Println("=== LibPyro Go Embedding Example ===")
	vm := pyro.NewVM()
	vm.RegisterNative("goMultiply", func(args []any) (any, error) {
		if len(args) < 2 {
			return 0, nil
		}
		a := args[0].(int)
		b := args[1].(int)
		return a * b, nil
	})

	vm.SetGlobal("appName", "EmbeddedGoApp")
	val, ok := vm.GetGlobal("appName")
	if ok {
		fmt.Printf("Global appName = %v\n", val)
	}

	_, err := vm.Eval(`print(goMultiply(6, 7));`)
	if err != nil {
		fmt.Printf("Go Eval Error: %v\n", err)
	}
}
