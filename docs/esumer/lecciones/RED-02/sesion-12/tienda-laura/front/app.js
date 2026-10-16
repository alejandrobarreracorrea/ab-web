// ===================== FRONT: lo que reacciona (JavaScript) =====================
// Este archivo corre en el navegador de Camila. Cualquiera lo puede ver con F12:
// por eso aquí NO van claves, ni precios "de verdad", ni reglas del negocio.
var API = window.API_URL; // la dirección de tu API Gateway (la escribe Terraform en config.js)

var CATALOGO = [
  { id: "camiseta", nombre: "Camiseta Laura", texto: "Algodón peinado, corte relajado.", precio: 59000, color: "#59c3ff", fondo: "#e3f4ff" },
  { id: "gorra", nombre: "Gorra Laura", texto: "Visera curva, ajuste trasero.", precio: 39000, color: "#16182b", fondo: "#efeefe" },
  { id: "buzo", nombre: "Buzo Laura", texto: "Felpa gruesa con capota.", precio: 119000, color: "#ff7aa8", fondo: "#ffe8f0" }
];

var $ = function (id) { return document.getElementById(id); };
var pesos = function (n) { return "$" + Number(n || 0).toLocaleString("es-CO"); };
var esc = function (t) { return String(t == null ? "" : t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
var ultimos = {};

// ---------- la colección ----------
$("productos").innerHTML = CATALOGO.map(function (p) {
  return '<button type="button" class="producto" data-id="' + p.id + '">' +
    '<div class="producto-foto" style="background:' + p.fondo + '"><svg viewBox="0 0 220 220" style="color:' + p.color + '"><use href="#ico-' + p.id + '"/></svg></div>' +
    "<h3>" + p.nombre + "</h3><p>" + p.texto + "</p>" +
    '<div class="precio">' + pesos(p.precio) + "<span>S · M · L · XL</span></div></button>";
}).join("");

function elegir(id) {
  $("producto").value = id;
  document.querySelectorAll(".producto").forEach(function (b) { b.classList.toggle("elegido", b.dataset.id === id); });
}
document.querySelectorAll(".producto").forEach(function (b) {
  b.addEventListener("click", function () { elegir(b.dataset.id); $("pedido").scrollIntoView(); $("cliente").focus(); });
});
$("producto").addEventListener("change", function () { elegir($("producto").value); });
elegir("camiseta");

// ---------- el viaje del pedido (animación de los 5 pasos) ----------
var pasos = document.querySelectorAll("#pasos li");
function marcar(n) {
  pasos.forEach(function (li, i) { li.classList.toggle("activo", i === n); li.classList.toggle("hecho", i < n); });
}
function terminar() { pasos.forEach(function (li) { li.classList.remove("activo"); li.classList.add("hecho"); }); }
function medir(r, total) {
  $("m-frio").textContent = r.frio ? "🥶 en frío" : "🔥 caliente";
  $("m-inst").textContent = r.instancia;
  $("m-ms").textContent = r.ms + " ms";
  $("m-total").textContent = total + " ms";
}

// ---------- hablar con el BACK ----------
function leerPedido() {
  return {
    cliente: $("cliente").value,
    producto: $("producto").value,
    talla: document.querySelector("input[name=talla]:checked").value,
    cantidad: Number($("cantidad").value || 1)
  };
}
function guardar(pedido) {
  return fetch(API + "/", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(pedido) })
    .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || j.message || ("error " + r.status)); return j; }); });
}
function leer(cliente) {
  var url = API + (cliente ? "/?cliente=" + encodeURIComponent(cliente) : "/");
  return fetch(url).then(function (r) { return r.json(); });
}

function pintar(r, nuevos) {
  var nombre = { camiseta: "Camiseta", gorra: "Gorra", buzo: "Buzo" };
  $("estado-lista").textContent = r.operacion + " · " + r.pedidos.length + " pedido(s) · " + (r.frio ? "🥶 en frío" : "🔥 caliente") + " · " + r.ms + " ms";
  $("lista").innerHTML = r.pedidos.map(function (p) {
    var hora = new Date(String(p.fecha).split("#")[0]).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    var nuevo = nuevos && nuevos[p.fecha] ? ' class="nuevo"' : "";
    return "<tr" + nuevo + "><td><b>" + esc(p.cliente) + "</b></td><td>" + esc(nombre[p.producto] || p.producto) + "</td><td>" + esc(p.talla) + "</td><td>" + esc(p.cantidad || 1) + "</td><td>" + pesos(p.total) + "</td><td>" + hora + '</td><td><span class="chip">' + esc(p.instancia) + "</span></td></tr>";
  }).join("") || '<tr><td colspan="7">Todavía no hay pedidos. ¡Haz el primero!</td></tr>';
}
function refrescar(cliente, nuevos) {
  return leer(cliente).then(function (r) { pintar(r, nuevos); }).catch(function () { $("estado-lista").textContent = "No pude leer los pedidos: revisa que terraform apply haya terminado."; });
}

// ---------- comprar ----------
$("formulario").addEventListener("submit", function (e) {
  e.preventDefault();
  var msg = $("mensaje"), boton = $("comprar"), t0 = performance.now();
  msg.className = "mensaje"; msg.textContent = "Enviando tu pedido…"; boton.disabled = true;
  marcar(0);
  setTimeout(function () { marcar(1); }, 250);
  var envio = guardar(leerPedido());
  setTimeout(function () { marcar(2); }, 550);
  envio.then(function (r) {
    marcar(3);
    var total = Math.round(performance.now() - t0);
    setTimeout(function () {
      marcar(4);
      setTimeout(terminar, 350);
      medir(r, total);
      msg.className = "mensaje ok";
      msg.textContent = "✅ ¡Listo, " + r.pedido.cliente + "! " + r.pedido.cantidad + " × " + r.pedido.producto + " talla " + r.pedido.talla + " · total " + pesos(r.pedido.total);
      ultimos = {}; ultimos[r.pedido.fecha] = 1;
      refrescar("", ultimos);
    }, 300);
  }).catch(function (err) {
    pasos.forEach(function (li) { li.classList.remove("activo", "hecho"); });
    msg.className = "mensaje error"; msg.textContent = "❌ " + err.message;
  }).finally(function () { boton.disabled = false; });
});

// ---------- botones de la lista ----------
$("ver-todos").onclick = function () { refrescar(""); };
$("ver-mios").onclick = function () {
  var c = $("cliente").value.trim();
  if (!c) { $("estado-lista").textContent = "Escribe tu nombre arriba para buscar solo tus pedidos."; return; }
  refrescar(c);
};

// ---------- la ráfaga: 20 pedidos al mismo tiempo ----------
$("rafaga").onclick = function () {
  var base = leerPedido(), boton = $("rafaga"), out = $("rafaga-resultado");
  if (!base.cliente) base.cliente = "rafaga";
  boton.disabled = true; out.textContent = "⚡ enviando 20 pedidos al mismo tiempo…";
  var envios = [];
  for (var i = 0; i < 20; i++) envios.push(guardar(base).catch(function () { return null; }));
  Promise.all(envios).then(function (rs) {
    var inst = {}, frios = 0, frenados = 0, nuevos = {};
    rs.forEach(function (r) {
      if (!r || !r.instancia) { frenados++; return; }
      inst[r.instancia] = (inst[r.instancia] || 0) + 1;
      if (r.frio) frios++;
      nuevos[r.pedido.fecha] = 1;
    });
    var n = Object.keys(inst).length;
    out.textContent = "⚡ " + (20 - frenados) + " de 20 pedidos guardados · AWS prendió " + n + " instancia(s) de la Lambda (" + frios + " en frío)\n" +
      Object.keys(inst).map(function (k) { return "   instancia " + k + " → " + inst[k] + " pedido(s)"; }).join("\n") +
      (frenados ? "\n🚦 " + frenados + " frenado(s) por el límite de concurrencia de tu cuenta (las cuentas nuevas pueden empezar con 10)" : "");
    return refrescar("", nuevos);
  }).finally(function () { boton.disabled = false; });
};

// al abrir la página: muestra los pedidos que ya existen
if (!API) { $("estado-lista").textContent = "Falta config.js: esta página debe abrirse desde tu_tienda (CloudFront)."; }
else refrescar("");
