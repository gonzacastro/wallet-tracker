# wallet-tracker

**Seguimiento real de tu cartera de inversiones argentina.** Baja el historial completo de tu
cuenta de **Portfolio Personal Inversiones (PPI)** y de **Binance**, lo guarda en una base
SQLite local, y calcula lo que las apps de los brokers no te muestran: desde cuándo tenés cada
instrumento, a qué precio lo compraste, cuánto ganaste o perdiste con cada uno, y cómo
evolucionó tu plata — en pesos y en dólares.

Todo corre en tu máquina. Las credenciales quedan en un `.env` local y los datos en un archivo
SQLite. **Nada se sube a ningún lado.**

```bash
wallet-tracker ver
```

Un comando: sincroniza, arma el reporte y lo abre en el navegador.

---

## El problema que resuelve

La app de tu broker te dice cuánto tenés hoy. No te dice lo demás:

**1. Desde cuándo y a qué precio.**
Si compraste una especie en tres tandas, tu costo promedio real no está en ninguna pantalla.
`wallet-tracker` arma la pila FIFO de cada especie: qué lote compraste cuándo, cuál sigue
abierto, y qué resultado realizaste al vender.

**2. Medir en pesos esconde la mitad de la historia.**
Una cartera que sube 11% en pesos mientras el dólar sube 5% rindió 6%, no 11%. Todo se calcula
también en dólares, con el CCL implícito del día de cada operación.

**3. Los canjes de CEDEAR rompen el costo promedio.**
Cuando un CEDEAR cambia de ratio, tus 40 nominales pasan a 120 y el precio se divide por tres.
Si nadie lo ajusta, la especie aparece con un −61% que nunca ocurrió — y el gráfico de precio,
con un precipicio que tampoco.

**4. Y si además tenés cripto, son dos apps y ninguna suma.**
PPI y Binance van a la misma base y a la misma tabla. Bitcoin se trata igual que un CEDEAR:
mismo motor FIFO, mismo costo promedio, mismo mini-gráfico.

## Qué vas a ver

Al abrir el reporte, arriba de todo:

```
QUE MIRAR HOY
· Hace 2 meses que no aportas
· TSLA viene -18,4%
· Tenes $520.000 sin invertir (4,1% de la cartera)
· Estas en tu mejor momento medido en dolares
```

Solo aparece lo que amerita que hagas algo. La mayoría de los días no dice nada.

Después, las tarjetas de situación:

| Valuación total | En USD | Capital aportado | Ganancia | Este mes | Peor momento | vs. S&P 500 |
|---|---|---|---|---|---|---|
| $12.600.000 | US$7.850 | $11.000.000 | +$1.600.000 | +3,2% | −14,5% | −2,1 pts |

Y las secciones:

- **Cómo viene cada instrumento** — una tarjeta por especie con su retorno total (precio +
  dividendos − costos), su peso en la cartera y un mini-gráfico del precio desde tu primera
  compra, con un punto en cada operación tuya.
- **Cuándo se movió cada una** — mapa de calor especie × mes. El total de una especie no
  distingue entre caer de a poco y desplomarse un mes puntual, y son cosas distintas.
- **Si tenés plata para poner** — escribís un monto y te dice cuánto va a cada especie y
  cuántos nominales comprar, para acercarte al reparto que definiste con los deslizadores.
- **Capital aportado y ganancia** — la escalera de tus aportes y, por encima, el rendimiento.
- **Mes a mes** — cuánto pusiste, cuánto vale y cuánta ganancia hay encima, mes por mes.

Sin siglas que haya que estudiar. Si querés TIR, TWR y volatilidad, están detrás de
`wallet-tracker resumen --avanzado`.

---

## Instalación

Requiere Python 3.10 o más nuevo.

```bash
git clone https://github.com/TU-USUARIO/wallet-tracker.git
cd wallet-tracker
uv venv && uv pip install -e .
source .venv/bin/activate
```

Con `pip` común:

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e .
```

### Probarlo sin conectar nada

```bash
wallet-tracker demo             # carga una cartera sintética
wallet-tracker resumen --demo
wallet-tracker reporte --demo --abrir
```

La cartera de ejemplo vive en un archivo aparte y **nunca** toca tu base real: `demo` se niega
a escribir sobre una base con movimientos reales, y `sync` se niega a bajar datos sobre una
base de ejemplo.

---

## Configuración

### PPI

1. Entrá a tu cuenta → pestaña **Gestiones** → activá el **servicio API** y generá las claves.
   Requisito de PPI: tener la cuenta abierta y haberla fondeado al menos una vez.
2. `wallet-tracker init` crea el `.env`.
3. Completá `PPI_PUBLIC_KEY` y `PPI_PRIVATE_KEY`.

### Binance (opcional)

1. **Perfil → API Management → Create API**.
2. **Permisos de solo lectura**: dejá activado únicamente *Enable Reading*. Desactivá *Enable
   Spot & Margin Trading* y *Enable Withdrawals*. Si podés, restringí por IP.
3. Completá `BINANCE_API_KEY` y `BINANCE_API_SECRET`.

Sin claves de Binance no pasa nada: esa parte simplemente no aparece.

> El `.env` está en `.gitignore` y nunca se commitea. El prefijo `PPI_` en algunas variables
> generales (`PPI_DB_PATH`, `PPI_BENCHMARK`) es histórico, de cuando el proyecto solo hablaba
> con PPI.

---

## Comandos

```bash
wallet-tracker ver                       # sincroniza, arma el reporte y lo abre
wallet-tracker ver --sin-bajar           # sin tocar la red, con lo que ya hay

wallet-tracker resumen                   # el panel en la terminal
wallet-tracker resumen --avanzado        # agrega TIR, TWR y volatilidad
wallet-tracker resumen --todo            # incluye los cambios de moneda
wallet-tracker aportar 500000            # a dónde mandar un aporte, desde la terminal

wallet-tracker instrumento GGAL          # lotes, operaciones y movimientos de una especie
wallet-tracker movimientos --sin-clasificar
wallet-tracker exportar -o exports/      # 5 CSV para Excel / Sheets

wallet-tracker init                      # crea .env y la base local
wallet-tracker sync --desde 2019-01-01   # fuerza re-descarga desde una fecha
```

**No hace falta borrar nada nunca.** `sync` es incremental: se fija hasta dónde bajó la última
vez, retrocede unos días por las cargas retroactivas, y pide solo lo nuevo. Cada movimiento
tiene un hash estable, así que volver a bajarlo lo pisa en vez de duplicarlo.

De hecho conviene **no** borrar la base: cada sincronización guarda una foto de tus tenencias, y
la API de los brokers solo te da la de hoy. Movimientos, órdenes y precios se vuelven a bajar
cuando quieras; las fotos viejas, no.

---

## Cómo funciona

```
API PPI ─┐
         ├─► SQLite ─► ledger ─► conversions ─► corporate ─► lots ─► analysis ─► CLI / HTML
API Binance ─┘        (clasifica)  (monedas)     (canjes)   (FIFO)   (métricas)
```

1. **`sync`** trae de cada broker: movimientos, órdenes, tenencias, series de precios y el
   dólar implícito. Es incremental.
2. **`ledger`** traduce la descripción en texto de cada movimiento a una categoría (compra,
   venta, dividendo, renta, comisión, impuesto, aporte, retiro…).
3. **`conversions`** detecta los cambios de moneda hechos con un título como vehículo y
   los saca del camino del FIFO: mueven plata entre bolsillos, no son una inversión.
4. **`corporate`** aplica los canjes y cambios de ratio declarados.
5. **`lots`** arma la pila FIFO de cada especie: qué lote compraste cuándo, cuál sigue abierto,
   qué resultado realizaste al vender.
6. **`analysis`** cruza todo con precios y dólar para calcular valuación, resultado y tasas.

Los pasos 2 a 4 arman el ledger **una sola vez**, en `analysis.load_events()`. Todo lo que
corrige la lectura cruda de los brokers pasa por ahí, para que el FIFO, la serie de valuación y
los flujos de la TIR vean exactamente los mismos eventos.

De los 20 módulos, **solo 4 saben de dónde vienen los datos** (`ppi_api`, `sync`,
`binance_api`, `binance_sync`). Todo lo demás recibe movimientos con fecha, ticker, cantidad,
importe y moneda, y no le importa si son de un CEDEAR o de bitcoin.

### Desde cuándo mide

El panel arranca en tu **primera inversión de verdad**, no en el primer movimiento del
historial. Si antes usaste la cuenta para comprar dólares, promediar esos años diluye el
rendimiento en períodos en los que no había cartera.

### El dólar implícito

No hay una cotización oficial útil, así que se calcula: el precio del mismo bono en pesos
dividido por su precio en dólares es el tipo de cambio que el mercado está pagando de verdad.
`GD30 / GD30C` por defecto, que da el CCL; `PPI_CCL_TICKER_USD=GD30D` lo mide al MEP.

De ahí sale todo lo que ves en dólares: la valuación, la conversión de los saldos en moneda
extranjera —incluidos los USDT de Binance— y la TIR en dólares.

### Métricas

- **Costo promedio ponderado (PPC)** y **fecha de la primera compra** de cada especie.
- **Retorno total por especie**: precio + dividendos − costos. Los dividendos se imputan a la
  especie que los pagó aunque se cobren en otra moneda.
- **Resultado realizado** (ventas apareadas FIFO) y **no realizado** (a precio de hoy).
- **TIR anual (XIRR)** por especie y de cartera, en pesos y en dólares.
- **TWR** (time-weighted return): rendimiento de la estrategia, neutro a aportes y retiros.
- **Volatilidad** y **peor caída**, medidas sobre el índice de retorno y no sobre la valuación
  cruda: sacar plata de la cuenta no es una pérdida.

---

## Cuando algo no cuadra

**Movimientos sin clasificar.** El clasificador se basa en descripciones de texto y hay
variantes que no conozco. Revisalos con `wallet-tracker movimientos --sin-clasificar` y agregá
reglas propias en un `rules.json` en la raíz (hay un `rules.example.json`). Se aplican antes que
las reglas por defecto, así que también sirven para corregir una clasificación errónea.

**"El broker informa N nominales y el historial da M".** Cada corrida concilia lo calculado
contra la foto de tenencias del broker. Si el desvío es un múltiplo limpio, casi siempre es un
**cambio de ratio de un CEDEAR**. El aviso te sugiere la línea exacta para
`corporate_actions.json`:

```json
[
  { "ticker": "SPY", "date": "2026-05-29", "ratio": 3,
    "note": "cambio de ratio del CEDEAR 1:3" }
]
```

Se aplica en esa fecha: los lotes anteriores se reescalan (misma plata, más papeles) y la serie
de valuación sigue siendo correcta antes y después.

**"Sin historial de compra en X".** El broker informa una tenencia que el historial no explica
—compraste antes del inicio del historial, o por una vía que la API no expone. El programa
**no inventa un costo**: la tenencia se muestra con cantidad, valor y gráfico, y el retorno
dice *costo desconocido*.

**"Se vendieron X sin compra previa".** Tu historial arranca después de esa compra: bajá
`PPI_HISTORY_START` y corré `wallet-tracker sync --desde 2018-01-01`.

---

## Límites conocidos

- **Compras de cripto por P2P, tarjeta o subcuenta** no aparecen en ningún endpoint de la API de
  Binance. El saldo se ve bien, pero sin costo.
- **Conversiones cripto-a-cripto** (ETH → BTC) se saltean: son comprar y vender a la vez, y
  mezclarían el costo de las dos puntas.
- **Bonos que amortizan**: renta y amortización se cuentan como ingreso pero no ajustan el costo
  unitario del lote. El resultado total de la especie es correcto; el "% sobre el precio" de un
  bono amortizado, no.
- **Canjes y splits** no se infieren solos: se detecta la discrepancia contra el broker y se
  avisa, pero el ratio lo declarás vos.
- **Una especie con dos monedas** se lleva como dos tenencias separadas, a propósito: el mismo
  ticker puede ser dos instrumentos distintos (7 SPY del ETF son 140 CEDEARs de SPY).
- **El tipo de instrumento** sale de tus propias tenencias, no del buscador de la API: muchos
  tickers existen dos veces (`AAPL` es una acción de NYSE y también un CEDEAR de BYMA).
- La API de PPI expone órdenes y movimientos, no el estado de tenencias a una fecha pasada: la
  serie histórica de valuación es una **reconstrucción**, no un dato oficial del broker.

---

## Estructura

```
src/wallet_tracker/
├── config.py       Configuración desde .env
├── db.py           Esquema SQLite y helpers
├── ppi_api.py      Wrapper del SDK oficial de PPI
├── sync.py         Descarga incremental de PPI
├── binance_api.py  Cliente de Binance (saldos, operaciones, precios)
├── binance_sync.py Descarga de Binance a la misma base
├── ledger.py       Clasificación de movimientos
├── money.py        Monedas y conversión a pesos
├── conversions.py  Cambios de moneda hechos con un título de vehículo
├── corporate.py    Canjes y conciliación contra el broker
├── plan.py         Reparto de aportes contra un objetivo
├── lots.py         Motor FIFO: lotes, costo, resultado realizado
├── metrics.py      XIRR, TWR, volatilidad, drawdown
├── valuation.py    Serie diaria de valuación en ARS y USD
├── analysis.py     Ensamble de todo
├── attention.py    Qué mirar hoy
├── report.py       HTML autocontenido con gráficos
├── console.py      Tablas de terminal
├── demo.py         Cartera sintética
└── cli.py          Comandos
```

El reporte HTML es un solo archivo sin dependencias externas: los gráficos son SVG generado a
mano y el repartidor de aportes es JavaScript inline. Se abre sin conexión.

## Tests

```bash
pytest
```

238 tests. Incluyen la conciliación contra la foto de tenencias del broker, la coherencia entre
`.env.example` y la plantilla del código, y —si tenés `node` instalado— una comparación del
repartidor de aportes entre su implementación en Python y su port a JavaScript, para que no se
desincronicen.

---

## Contribuir

El clasificador de movimientos se basa en las descripciones de texto de PPI, que varían. Si
encontrás una que no reconoce, un PR con la regla es bienvenido.

Lo mismo con otros brokers: agregar uno es escribir un cliente y un normalizador que devuelva
movimientos con fecha, ticker, cantidad, importe y moneda. Todo lo demás ya funciona.

---

Herramienta de seguimiento personal. **No es asesoramiento de inversión** ni tiene relación
oficial con Portfolio Personal Inversiones ni con Binance. Los precios provienen del histórico
de cada broker y pueden diferir del cierre oficial.

Documentación de la API de PPI: <https://itatppi.github.io/ppi-official-api-docs/>
