/*
 * Example: Embedding Pyro VM in Node.js Application
 */
const { BurnoutVM } = require('../../../Burnout/embed/node/index.js');

console.log("=== LibPyro Node.js Embedding Example ===");

const vm = new BurnoutVM({ sandbox: true });

vm.registerNative('jsGreet', (name) => {
  return `Hello from Node JS to ${name}!`;
});

vm.setGlobal('environment', 'NodeJS Host');

console.log("Evaluating Cryo code inside Node VM...");
vm.eval(`string greeting = jsGreet("Developer"); print(greeting);`);
console.log("Node JS Embedding Completed Successfully.");
