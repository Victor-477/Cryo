// glue.js — loads app.wasm (Cryo compiled with --backend wasm) and drives the DOM.
// WASM exports use i64, which surfaces in JS as BigInt.
let wasm = null;

async function boot() {
  const status = document.getElementById('status');
  try {
    const res = await fetch('app.wasm');
    const bytes = await res.arrayBuffer();
    const { instance } = await WebAssembly.instantiate(bytes, {
      env: { log: (x) => console.log('[cryo]', x.toString()) },
    });
    wasm = instance.exports;
    status.textContent = 'WebAssembly loaded — Cryo running in the browser.';
    status.className = 'ok';
    update();
  } catch (e) {
    status.textContent = 'Failed to load WASM: ' + e;
  }
}

function update() {
  if (!wasm) return;
  const n = BigInt(document.getElementById('n').value || '0');
  document.getElementById('fib').textContent = wasm.fib(n).toString();
  document.getElementById('sq').textContent = wasm.square(n).toString();
  document.getElementById('fact').textContent = wasm.factorial(n).toString();
  document.getElementById('sum').textContent = wasm.sum_to(n).toString();
}

window.addEventListener('DOMContentLoaded', boot);
