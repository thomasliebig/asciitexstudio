# Inspecting the BrowserFS project and render cache

AsciiTeX Studio stores project files and its render cache locally in IndexedDB through BrowserFS. The following snippets can be pasted into the Chrome DevTools Console while AsciiTeX Studio is open.

## Initialize the inspector

This helper resolves BrowserFS's inode-based storage into readable filenames and file contents.

```js
window.atx = await (async () => {
  const request = indexedDB.open("asciitex-studio", 1);
  const db = await new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  const get = key => new Promise((resolve, reject) => {
    const tx = db.transaction("asciitex-studio", "readonly");
    const request = tx.objectStore("asciitex-studio").get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  const bytes = value =>
    value instanceof Uint8Array ? value : new Uint8Array(value);

  const inode = value => {
    const data = bytes(value);
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    return {
      size: view.getUint32(0, true),
      mode: view.getUint16(4, true),
      atime: new Date(view.getFloat64(6, true)),
      mtime: new Date(view.getFloat64(14, true)),
      ctime: new Date(view.getFloat64(22, true)),
      dataId: new TextDecoder("ascii").decode(data.slice(30))
    };
  };

  const rootInode = inode(await get("/"));
  const rootListing = JSON.parse(
    new TextDecoder().decode(bytes(await get(rootInode.dataId)))
  );

  async function stat(name) {
    const inodeId = rootListing[name.replace(/^\//, "")];
    if (!inodeId) return undefined;
    return inode(await get(inodeId));
  }

  async function readBytes(name) {
    const info = await stat(name);
    if (!info) throw new Error(`File not found: ${name}`);
    return bytes(await get(info.dataId));
  }

  async function readText(name) {
    return new TextDecoder().decode(await readBytes(name));
  }

  return { db, get, stat, readBytes, readText, rootListing };
})();

console.log("AsciiTeX BrowserFS inspector ready:", atx);
```

## List all BrowserFS files

```js
console.table(
  await Promise.all(
    Object.keys(atx.rootListing).sort().map(async name => {
      const info = await atx.stat(name);
      return {
        name,
        bytes: info.size,
        modified: info.mtime.toLocaleString()
      };
    })
  )
);
```

This includes internal `.asciitex-*` files that are intentionally hidden from the Studio file explorer and project ZIP exports.

## Read project files

```js
console.log(await atx.readText("main.tex"));
console.log(await atx.readText("chapter.tex"));
```

Binary files can be inspected as bytes:

```js
console.log(await atx.readBytes("image.png"));
```

## Load the render cache

The cache filename is versioned. Find the current name first:

```js
const cacheName = Object.keys(atx.rootListing)
  .find(name => name.startsWith(".asciitex-render-cache-"));

console.log("Current cache:", cacheName);
window.atxCache = JSON.parse(await atx.readText(cacheName));
```

## Show a cache summary

```js
const entries = Object.values(atxCache.entries ?? {});
const documents = Object.values(atxCache.documents ?? {});

console.table({
  version: atxCache.version,
  generation: atxCache.generation,
  renderedBoxes: entries.length,
  completeDocuments: documents.length,
  cacheBytes: new Blob([JSON.stringify(atxCache)]).size
});
```

## Inspect cached boxes by activity

```js
console.table(
  Object.entries(atxCache.entries ?? {})
    .map(([key, entry]) => ({
      key: key.slice(0, 12),
      kind: entry.value?.kind,
      width: entry.value?.width ?? entry.value?.box?.width,
      lines:
        entry.value?.lines?.length ??
        entry.value?.box?.lines?.length,
      hits: entry.hits ?? 0,
      generation: entry.generation,
      touched: new Date(entry.touched * 1000).toLocaleString()
    }))
    .sort((a, b) => b.hits - a.hits)
);
```

## Inspect complete-document cache hits

```js
console.table(
  Object.entries(atxCache.documents ?? {}).map(([key, entry]) => ({
    key: key.slice(0, 12),
    characters: entry.output?.length ?? 0,
    hits: entry.hits ?? 0,
    touched: new Date(entry.touched).toLocaleString()
  }))
);
```

## Observe cache reuse

1. Compile the document once.
2. Reload `atxCache` using the cache-loading snippet.
3. Run the box and document tables again.
4. Compile the unchanged document once more.

An unchanged complete-document build should report `Built in 0 ms` in the Studio. Changed documents can still reuse matching intermediate boxes.

## Chrome Application panel

The same data is visible under:

1. Open Chrome DevTools.
2. Select **Application**.
3. Expand **IndexedDB**.
4. Open **asciitex-studio → asciitex-studio**.

BrowserFS stores inode and data records under generated IDs, so the Console helper is generally easier to use than the raw table.

## Cache cleanup behavior

AsciiTeX Studio automatically:

- removes cache files from older renderer versions;
- removes intermediate objects unused for eight cache generations;
- removes entries untouched for seven days;
- retains at most 800 intermediate objects;
- retains at most twelve complete-document variants.

Do not edit inode records manually. Incorrect records can make the BrowserFS project unreadable.
