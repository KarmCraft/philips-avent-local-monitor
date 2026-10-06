const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../src/web/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const camera = { name: 'Test monitor', viewer_url: 'http://127.0.0.1:8889/baby' };
const flush = () => new Promise(resolve => setImmediate(resolve));

// Exercise the actual page script, with only browser/media/network boundaries faked.
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.options = [];
    this.value = '';
    this.volume = 1;
    this.muted = false;
    this.srcObject = null;
    this.playCalls = 0;
    this.pauseCalls = 0;
    this.classList = { toggle() {}, remove() {} };
  }
  set innerHTML(value) { this.children = []; }
  appendChild(child) { child.parent = this; this.children.push(child); }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  querySelector(selector) {
    return this.children.find(child => selector === '.empty'
      ? child.className === 'empty' : child.tagName === selector) || null;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  emit(name) { for (const callback of this.listeners[name] || []) callback({ target: this }); }
  play() { this.playCalls += 1; return this.playResult?.() || Promise.resolve(); }
  pause() { this.pauseCalls += 1; }
}

function track(kind) {
  return {
    kind, readyState: 'live', stopCalls: 0, listeners: {},
    stop() { this.stopCalls += 1; this.readyState = 'ended'; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    end() { this.readyState = 'ended'; this.listeners.ended?.(); }
  };
}

class Stream {
  constructor(tracks = []) { this.tracks = tracks; }
  getTracks() { return this.tracks; }
  getAudioTracks() { return this.tracks.filter(item => item.kind === 'audio'); }
  addTrack(item) { this.tracks.push(item); }
}

function fixture({ lazyReader = false } = {}) {
  const nodes = {};
  for (const match of html.matchAll(/id="([^"]+)"/g)) nodes[match[1]] = new Element('div');
  const head = new Element('head');
  const readers = [];
  class Reader {
    constructor(options) { this.options = options; this.closeCalls = 0; readers.push(this); }
    close() { this.closeCalls += 1; }
    receive(stream, item = stream.getTracks()[0]) {
      this.options.onTrack({ streams: stream ? [stream] : [], track: item });
    }
  }
  const window = new Element('window');
  const requests = [];
  const context = vm.createContext({
    document: { getElementById: id => nodes[id], createElement: tag => new Element(tag), head },
    window, URL, MediaStream: Stream, setInterval() {}, setTimeout,
    fetch: async url => {
      requests.push(url);
      return { ok: true, json: async () => ({ ok: true, configured: false, services: {}, feed: {} }) };
    },
    ...(lazyReader ? {} : { MediaMTXWebRTCReader: Reader })
  });
  vm.runInContext(script, context);
  return {
    nodes, readers, context, head, window, requests, Reader,
    async start(force = false) {
      await flush(); // Let the initial status request finish before starting a test feed.
      await context.loadViewer(camera, force);
      return nodes.viewer.querySelector('video');
    },
    async audio() {
      const video = await this.start();
      const stream = new Stream([track('video'), track('audio')]);
      readers.at(-1).receive(stream);
      return { video, stream };
    }
  };
}

test('page starts muted with a local audio-capable reader and native controls', async () => {
  const f = fixture();
  const { video } = await f.audio();
  assert.equal(video.muted, true);
  assert.equal(video.autoplay, true);
  assert.equal(video.controls, true);
  assert.equal(video.playsInline, true);
  assert.equal(f.readers[0].options.url, 'http://127.0.0.1:8889/baby/whep');
  assert.equal(f.nodes.mute.disabled, false);
  assert.equal(f.nodes.mute.textContent, 'Unmute');
  assert.equal(f.nodes.mute.attributes['aria-label'], 'Unmute live audio');
});

test('mute toggles without reconnecting, stopping tracks or touching recording APIs', async () => {
  const f = fixture();
  const { video, stream } = await f.audio();
  f.nodes.mute.emit('click');
  assert.equal(video.muted, false);
  assert.equal(f.nodes.mute.textContent, 'Mute');
  f.nodes.mute.emit('click');
  assert.equal(video.muted, true);
  assert.equal(f.readers.length, 1);
  assert.equal(f.readers[0].closeCalls, 0);
  assert.ok(stream.getTracks().every(item => item.stopCalls === 0));
  assert.ok(f.requests.every(url => url === '/api/status'));
});

test('native volume controls update the toolbar, including zero volume', async () => {
  const f = fixture();
  const { video } = await f.audio();
  video.muted = false;
  video.emit('volumechange');
  assert.equal(f.nodes.mute.textContent, 'Mute');
  video.volume = 0;
  video.emit('volumechange');
  assert.equal(f.nodes.mute.textContent, 'Unmute');
  f.nodes.mute.emit('click');
  assert.equal(video.volume, 1);
  assert.equal(video.muted, false);
});

test('status refresh does not recreate the player; explicit reload does and keeps sound choice', async () => {
  const f = fixture();
  const { video, stream } = await f.audio();
  f.nodes.mute.emit('click');
  await f.context.loadViewer(camera);
  assert.equal(f.readers.length, 1);
  const replacement = await f.start(true);
  assert.equal(f.readers.length, 2);
  assert.equal(f.readers[0].closeCalls, 1);
  assert.ok(stream.getTracks().every(item => item.stopCalls === 1));
  assert.equal(video.srcObject, null);
  assert.equal(replacement.muted, false);
});

test('offline cleanup and resumed feed preserve the mute choice', async () => {
  const f = fixture();
  const { video, stream } = await f.audio();
  f.nodes.mute.emit('click');
  f.context.showViewerMessage('Offline');
  assert.equal(f.readers[0].closeCalls, 1);
  assert.equal(video.srcObject, null);
  assert.ok(stream.getTracks().every(item => item.stopCalls === 1));
  assert.equal(f.nodes.mute.disabled, true);
  const resumed = await f.start();
  assert.equal(resumed.muted, false);
  assert.equal(f.readers.length, 2);
});

test('reader retries clear stale tracks and recover in the same reader', async () => {
  const f = fixture();
  const { video, stream } = await f.audio();
  f.nodes.mute.emit('click');
  f.readers[0].options.onError('disconnected');
  assert.equal(video.srcObject, null);
  assert.ok(stream.getTracks().every(item => item.stopCalls === 1));
  assert.equal(f.nodes.mute.disabled, true);
  const resumed = new Stream([track('video'), track('audio')]);
  f.readers[0].receive(resumed);
  assert.equal(video.muted, false);
  assert.equal(video.srcObject, resumed);
  assert.equal(f.readers.length, 1);
  assert.equal(f.nodes.mute.disabled, false);
});

test('no audio or an ended audio track disables the sound button', async () => {
  const f = fixture();
  await f.start();
  f.readers[0].receive(new Stream([track('video')]));
  assert.equal(f.nodes.mute.disabled, true);
  const audio = track('audio');
  const stream = new Stream([audio]);
  f.readers[0].receive(stream, audio);
  assert.equal(f.nodes.mute.disabled, false);
  audio.end();
  assert.equal(f.nodes.mute.disabled, true);
});

test('pagehide releases the reader and receiver tracks', async () => {
  const f = fixture();
  const { video, stream } = await f.audio();
  f.window.emit('pagehide');
  assert.equal(f.readers[0].closeCalls, 1);
  assert.equal(video.srcObject, null);
  assert.ok(stream.getTracks().every(item => item.stopCalls === 1));
});

test('late tracks from an obsolete reader are stopped, not attached', async () => {
  const f = fixture();
  await f.audio();
  await f.start(true);
  const obsolete = track('audio');
  f.readers[0].receive(new Stream([obsolete]));
  assert.equal(obsolete.stopCalls, 1);
  assert.equal(f.nodes.viewer.querySelector('video').srcObject, null);
});

test('autoplay rejection falls back to muted and asks for a user gesture', async () => {
  const f = fixture();
  const { video } = await f.audio();
  video.playResult = () => video.muted ? Promise.resolve()
    : Promise.reject(Object.assign(new Error('gesture required'), { name: 'NotAllowedError' }));
  f.nodes.mute.emit('click');
  await flush();
  assert.equal(video.muted, true);
  assert.equal(f.nodes.mute.textContent, 'Unmute');
  assert.match(f.nodes.audioStatus.textContent, /Click Unmute/);
  assert.equal(f.readers.length, 1);
});

test('reader script load is shared and late load cannot revive an offline player', async () => {
  const f = fixture({ lazyReader: true });
  await flush();
  const first = f.context.loadViewer(camera);
  const second = f.context.loadViewer(camera, true);
  assert.equal(f.head.children.length, 1);
  const loader = f.head.children[0];
  assert.equal(loader.src, 'http://127.0.0.1:8889/baby/reader.js');
  f.context.showViewerMessage('Offline');
  f.context.MediaMTXWebRTCReader = f.Reader;
  loader.onload();
  await Promise.all([first, second]);
  assert.equal(f.readers.length, 0);
  assert.equal(f.head.children.length, 0);
});

test('failed reader script can be retried rather than caching rejection', async () => {
  const f = fixture({ lazyReader: true });
  await flush();
  const first = f.context.loadViewer(camera);
  f.head.children[0].onerror();
  await first;
  assert.equal(f.readers.length, 0);
  assert.equal(f.head.children.length, 0);
  const second = f.context.loadViewer(camera);
  f.context.MediaMTXWebRTCReader = f.Reader;
  f.head.children[0].onload();
  await second;
  assert.equal(f.readers.length, 1);
});

test('streamless track events still attach live audio', async () => {
  const f = fixture();
  const video = await f.start();
  const audio = track('audio');
  f.readers[0].receive(null, audio);
  assert.deepEqual(video.srcObject.getAudioTracks(), [audio]);
  assert.equal(f.nodes.mute.disabled, false);
});
