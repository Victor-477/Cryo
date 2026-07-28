/*
 * Example: Embedding Pyro VM in C Application
 */
#include <stdio.h>
#include "../../../Burnout/embed/c_api/include/pyro_embed.h"

pyro_value_t host_add(pyro_vm_t* vm, const pyro_value_t* args, size_t argc, void* user_data) {
    if (argc < 2) return pyro_make_int(0);
    int64_t a = pyro_get_int(args[0]);
    int64_t b = pyro_get_int(args[1]);
    return pyro_make_int(a + b);
}

int main(void) {
    printf("=== LibPyro C Embedding Example ===\n");
    pyro_config_t cfg = { false, false };
    pyro_vm_t* vm = pyro_vm_create(&cfg);
    if (!vm) {
        fprintf(stderr, "Failed to create VM\n");
        return 1;
    }

    pyro_vm_register_native(vm, "host_add", host_add, NULL);

    pyro_value_t res;
    pyro_vm_eval(vm, "print(host_add(10, 20));", &res);
    pyro_value_free(res);

    pyro_vm_free(vm);
    printf("VM freed successfully\n");
    return 0;
}
