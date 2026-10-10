import html

from mini_crud import config
from mini_crud.store import Record, Store


def render(store: Store) -> str:
    records = store.all()
    rows = "\n".join(_row_html(record) for record in records)
    sidebar = "\n".join(
        f'<li class="sidebar-item" data-id="{html.escape(record.id)}">'
        f"{html.escape(record.name)}</li>"
        for record in records
    )
    return f"""<!DOCTYPE html>
<html lang="es" translate="no">
<head>
  <meta charset="utf-8">
  <meta name="google" content="notranslate">
  <title>{html.escape(config.APP_NAME)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; }}
    header {{
      padding: 1rem 1.5rem; border-bottom: 1px solid #ccc;
      display: flex; gap: 1.5rem; flex-wrap: wrap;
    }}
    .layout {{ display: flex; min-height: calc(100vh - 4rem); }}
    aside {{ width: 12rem; border-right: 1px solid #ccc; padding: 1rem; list-style: none; }}
    aside ul {{ padding: 0; margin: 0; }}
    aside li {{ padding: 0.35rem 0; }}
    main {{ flex: 1; padding: 1rem 1.5rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 0.4rem; text-align: left; }}
    th {{ background: #f4f4f4; }}
    .toolbar {{
      margin-bottom: 1rem; display: flex; gap: 0.75rem;
      align-items: center; flex-wrap: wrap;
    }}
    #aviso-guardado {{ color: #0a6b0a; font-weight: 600; min-height: 1.25rem; }}
    .hidden {{ display: none; }}
    input[type="search"] {{ min-width: 14rem; padding: 0.35rem; }}
    button {{ padding: 0.35rem 0.75rem; cursor: pointer; }}
  </style>
</head>
<body>
  <header>
    <span id="app-name">{html.escape(config.APP_NAME)}</span>
    <span id="app-version">v{html.escape(config.APP_VERSION)}</span>
    <span id="app-hoy">{html.escape(config.TODAY_LABEL)}: {html.escape(config.TODAY_TEXT)}</span>
  </header>
  <div class="layout">
    <aside aria-label="Lista lateral">
      <ul id="sidebar-list">
        {sidebar}
      </ul>
    </aside>
    <main>
      <div class="toolbar">
        <label for="busqueda">Buscar</label>
        <input type="search" id="busqueda" name="busqueda"
               placeholder="Filtrar por nombre" autocomplete="off">
        <button type="button" id="btn-alta">Alta</button>
        <button type="button" id="btn-borrar">Borrar</button>
      </div>
      <p id="aviso-guardado" aria-live="polite"></p>
      <table id="rejilla">
        <thead>
          <tr><th>Nombre</th><th>Puesto</th><th>Nota</th></tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </main>
  </div>
  <script>
    const aviso = document.getElementById("aviso-guardado");
    const SAVE_NOTICE = "Se guardó el cambio.";
    let selectedId = document.querySelector("#rejilla tbody tr")?.dataset.id || "";

    function showSaved() {{
      aviso.textContent = SAVE_NOTICE;
    }}

    function postJson(url, body) {{
      return fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(body),
      }}).then((r) => {{
        if (!r.ok) throw new Error("request failed");
        return r.json().catch(() => ({{}}));
      }});
    }}

    function applyFilter() {{
      const q = document.getElementById("busqueda").value.trim().toLowerCase();
      document.querySelectorAll("#rejilla tbody tr").forEach((row) => {{
        const name = row.querySelector('[data-field="name"]')?.value.toLowerCase() || "";
        row.classList.toggle("hidden", q !== "" && !name.includes(q));
      }});
    }}

    document.getElementById("busqueda").addEventListener("input", applyFilter);

    document.querySelectorAll("#rejilla tbody tr").forEach((row) => {{
      row.addEventListener("click", () => {{
        selectedId = row.dataset.id;
        document.querySelectorAll("#rejilla tbody tr").forEach((r) => {{
          r.classList.remove("selected");
        }});
        row.classList.add("selected");
      }});
    }});

    document.querySelectorAll("#rejilla [data-field]").forEach((input) => {{
      input.addEventListener("blur", () => {{
        const row = input.closest("tr");
        const id = row.dataset.id;
        const field = input.dataset.field;
        const value = input.value;
        postJson(`/registros/${{id}}/celda`, {{ field, value }}).then(() => showSaved());
      }});
    }});

    document.getElementById("btn-alta").addEventListener("click", () => {{
      const name = prompt("Nombre del aparato:");
      if (!name) return;
      const bench = prompt("Puesto:") || "";
      const note = prompt("Nota:") || "";
      postJson("/registros", {{ name, bench, note }}).then(() => location.reload());
    }});

    document.getElementById("btn-borrar").addEventListener("click", () => {{
      if (!selectedId) {{
        alert("Selecciona una fila en la rejilla.");
        return;
      }}
      if (!confirm("¿Borrar el aparato seleccionado?")) return;
      postJson(`/registros/${{selectedId}}/borrar`, {{}}).then(() => location.reload());
    }});
  </script>
</body>
</html>"""


def _row_html(record: Record) -> str:
    return f"""<tr data-id="{html.escape(record.id)}">
  <td><input data-field="name" value="{html.escape(record.name)}"></td>
  <td><input data-field="bench" value="{html.escape(record.bench)}"></td>
  <td><input data-field="note" value="{html.escape(record.note)}"></td>
</tr>"""
