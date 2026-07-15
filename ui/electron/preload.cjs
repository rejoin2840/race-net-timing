'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Map each caller callback to its IPC wrapper so offRows can actually
// remove the listener that onRows registered (a fresh wrapper would never match).
const wrappers = new Map();

contextBridge.exposeInMainWorld('racenet', {
  onRows(cb) {
    const wrapper = (_e, data) => cb(data);
    wrappers.set(cb, wrapper);
    ipcRenderer.on('rows-update', wrapper);
  },
  offRows(cb) {
    const wrapper = wrappers.get(cb);
    if (wrapper) {
      ipcRenderer.removeListener('rows-update', wrapper);
      wrappers.delete(cb);
    }
  },
});
