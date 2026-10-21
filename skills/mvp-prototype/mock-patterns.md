# Mock patterns, by target

The mock is the fragment `build` puts in the main area. It follows the plan's opening diagram. Keep it short: enough to click
through every feature, fake data inline, no external URLs. Combined targets stack two patterns (a web screen above an api
sequence).

## web (also desktop, mobile)

Screens, one shown at a time; navigation with `data-go`.

```html
<section data-screen="S1" data-title="Note list">
  <h1 data-edit>My notes</h1>
  <button data-feature="F1" data-go="S2">Add</button>
  <input data-feature="F3" placeholder="Search notes">
  <ul data-sl-order>
    <li data-feature="F1">Groceries <span data-feature="F2">#home</span></li>
  </ul>
</section>
<section data-screen="S2" data-title="New note">
  <textarea data-feature="F1">Buy milk</textarea>
  <button data-go="S1">Save</button>
</section>
```

## cli

A terminal session: typed commands and their output, one block per feature.

```html
<div class="sl-term" data-sl-order>
  <div data-feature="F1">$ notes add "Buy milk"
saved note 1</div>
  <div data-feature="F2">$ notes list --tag home
1  Buy milk   #home</div>
  <div data-feature="F3">$ notes rm 9
error: no note 9 (exit 2)</div>
</div>
```

## library

Example calls with sample output, in the order a user would chain them. Use the target language's syntax.

```html
<div data-sl-order>
  <div class="sl-call" data-feature="F1"><code data-edit>store = open_store("notes.db")</code>
    <pre>Store(path="notes.db", notes=0)</pre></div>
  <div class="sl-call" data-feature="F2"><code>store.add("Buy milk", tags=["home"])</code>
    <pre>Note(id=1, title="Buy milk", tags=["home"])</pre></div>
</div>
```

## api (also service)

Request and response pairs, with the error cases. Paths only, never a scheme and host.

```html
<div data-sl-order>
  <div class="sl-call" data-feature="F1"><code>POST /notes {"title": "Buy milk"}</code>
    <pre>201 {"id": 1, "title": "Buy milk"}</pre></div>
  <div class="sl-call" data-feature="F2"><code>GET /notes?tag=home</code>
    <pre>200 [{"id": 1, "title": "Buy milk", "tags": ["home"]}]</pre></div>
  <div class="sl-call" data-feature="F1"><code>POST /notes {}</code>
    <pre>400 {"error": "title is required"}</pre></div>
</div>
```

## other (pipeline, bot, agent)

A data-flow mock: inputs, steps, outputs, and where the user steps in, each stage with sample data.

```html
<div data-sl-order>
  <div class="sl-call" data-feature="F1"><b>Input</b> inbox/*.eml <pre>3 files</pre></div>
  <div class="sl-call" data-feature="F2"><b>Classify</b> <pre>2 invoices, 1 other</pre></div>
  <div class="sl-call" data-feature="F3"><b>You review</b> <button data-edit>Approve batch</button></div>
  <div class="sl-call" data-feature="F4"><b>Output</b> ledger.csv <pre>2 rows added</pre></div>
</div>
```
