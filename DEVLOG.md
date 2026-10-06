# Development log — Digital World

Registro de decisiones, mediciones, pruebas y problemas pendientes. Todo lo que aquí figura como
"medido" o "probado" se ejecutó realmente en el entorno de desarrollo (Linux, Python 3.13.16,
pygame-ce 2.5.8, NumPy 2.5, 4 núcleos).

## Estado inicial

El repositorio estaba vacío (sin commits). Todo el proyecto se creó en esta sesión.

## Decisiones técnicas

| Decisión | Motivo |
|---|---|
| **pygame-ce** para el render 2D | Disponible por pip en Windows/Linux/macOS, sin motor externo, suficiente para una vista cenital de casillas con iluminación por capas; Panda3D/3D habría multiplicado el coste de arte sin mejorar la simulación. |
| Simulación pura Python + NumPy puntual | El campo de corrupción, el ruido procedural y la generación de terreno usan NumPy; la IA por agente es Python (decenas de agentes detallados), con estructuras planas donde mide caro (A*). |
| 1 tick = 1 minuto del mundo, 10 ticks/s a 1× | Un día = 2,4 min reales a 1×; las tasas de necesidades, regeneración y corrupción se expresan por tick. |
| Regiones latentes serializadas (no objetos vivos) | La descarga es real: las criaturas latentes son datos; recargar reconstruye objetos desde datos, lo que prueba continuamente que la persistencia no depende de estado oculto. |
| Simulación abstracta por pasos de 1 h | Mismas tasas y mismo modelo de idoneidad que la simulación detallada, pero sin rutas ni percepción; las trayectorias de población de ambos modos resultaron similares (ver mediciones). |
| Utility AI con histéresis | Puntuaciones interpretables + motivos en texto; compromiso con la acción actual, inercia del sueño, prohibición temporal de objetivos fallidos y frustración de búsqueda evitan oscilaciones y bucles. |
| SQLite con escritura atómica | Transacción en un temporal, verificación, `os.replace`; `.bak` automático. |
| Arte y audio procedurales | Sin licencias ni recursos externos; reemplazables por archivos. |
| Sin LLM por criatura | Requisito: comportamiento local, barato y reproducible. |

## Errores encontrados por las pruebas y corregidos

1. **Pasos entre regiones bloqueados**: los corredores se tallaban en diagonal (8-conexos) pero la
   conectividad se calcula 4-conexa, así que el relleno de islas los volvía a cerrar. Se reescribió
   el tallado; verificado con 78 mundos generados (0 pasos inaccesibles).
2. **Sucesión de líderes no registrada**: la muerte del líder borraba `leader_id` antes de la
   elección, así que la crónica nunca anotaba el relevo.
3. **Oscilación comer↔huir ante un jugador quieto**: la amenaza del jugador ya no depende solo del
   miedo sino de la cercanía, y existe habituación (el miedo baja si el Wayfinder es inofensivo).
4. **Inanición en vez de migración**: una criatura hambrienta en una región sin comida seguía
   "buscando comida". La urgencia de migrar incluye ahora la angustia personal (hambre × escasez,
   sed, *taint*, miedo) y la frustración por búsquedas fallidas.
5. **Colapso de forraje mal medido**: se medía sobre los parches que quedaban; ahora es relativo a
   la capacidad histórica de la región.
6. **Expulsiones territoriales repetidas** (una criatura expulsada 19 veces): el veto a un
   territorio no se respetaba; ahora la exploración y la búsqueda de comida lo evitan y el dueño
   tiene un enfriamiento por intruso. Detectado con `tools.inspect why`.
7. **Sueño fragmentado y reagrupamiento que nunca terminaba** (estadísticas de `inspect profile`):
   inercia del sueño e histéresis en la distancia de reagrupamiento.
8. **Thirst deaths** tempranas: las criaturas no conocían su propia región; ahora heredan/conocen el
   agua y la comida cercanas y comparten conocimiento socialmente; olfato de agua a mayor distancia.
9. Colisiones de nombres de argumentos en el bus de eventos (`kind=`), caché de fuentes tras
   `pygame.quit()`, brillos aditivos que se veían como discos planos, exceso de líneas de glitch.

## Equilibrio ecológico (mediciones)

Ejecuciones de 30 días con el mundo por defecto (5×4 regiones), `longrun` en modo abstracto:

* Antes de ajustar: musgolings 56→3, glimmerhares 37→2, depredadores 14→1 (sobredepredación y
  reproducción bloqueada por un umbral rígido de comida).
* Tras ajustar (fertilidad continua según índice de comida, apetito de depredadores, reparto de
  presas en manada, menos manadas iniciales): semilla 1337 → musgolings 60→105→35→40, glimmerhares
  37→90→20→44, thornhorns 42→56→43; semilla 99 → musgolings 73→125, thornhorns 30→43. Las
  poblaciones oscilan sin extinción global; los depredadores llegan a mínimos (1–3) en algunas
  semillas y la inmigración desde el "Outer Static" (registrada en la crónica) actúa como válvula.
* Modo detallado 9 días (semilla 1337): trayectorias comparables al modo abstracto
  (musgolings 60→91→73, glimmerhares 37→82→59, thornhorns 42→64).

## Rendimiento (medido con `tools.inspect profile`)

* Mundo por defecto, ~50–80 criaturas detalladas en 5 regiones activas, ~220–400 en total:
  **2,8–3,4 ms por tick** (≈30× tiempo real sostenible a 10 ticks/s). El 80 % del coste es la
  actualización de criaturas; el A* plano tarda ≈1 ms por ruta larga aleatoria en 64×48.
* Mundo grande 8×6 (48 regiones, 585 criaturas): **2,6 ms por tick**; las regiones latentes cuestan
  ~0,02 ms/tick amortizado.
* Avance abstracto de 1 día de todo el mundo: 0,5–0,8 s.
* Guardado completo: ~50 ms, ~0,7 MB por partida de 3 días.
* Optimizaciones aplicadas por medición: A* con arrays planos (~3× más rápido que diccionarios),
  necesidades cada 2 ticks escalonadas, presupuesto de rutas por tick, líneas rectas sin A* cuando
  hay visión directa, cachés de listas de costes y de corrupción por casilla.

## Pruebas

`python -m pytest -q` → **71 pruebas, todas pasan** (≈21 s). Cubren: reloj y runner (independencia de
FPS, límites, presupuesto), RNG, eventos e historia con causas, contenido, generación (conectividad,
determinismo), necesidades, decisiones (comer, beber, descansar, huir, cazar, evitar corrupción,
ayuda en manada), recuperación ante objetivos inválidos, no oscilación, memoria y relaciones,
liderazgo y territorios (captura con historia), ecología, corrupción (propagación entre regiones,
balizas, estabilización, erosión), migración (destino viable por conexión real, identidad sin
duplicar, penalización por regiones recién abandonadas), LOD (carga/descarga triple sin pérdida de
memoria/relaciones, cambios durante la ausencia, LOD sigue al jugador), avance rápido, persistencia
(ida y vuelta completa, `.bak`, rechazo de duplicados, filas dañadas, migración v1→v2, formato
futuro, progreso offline, relación con el jugador tras reiniciar), interacciones del jugador
(alimentar, vínculo, acercamiento, espantar, curar, recolectar, construir/desmontar, territorio con
aviso y luego golpe, viaje entre regiones), determinismo por semilla, aislamiento de fallos, render
(vista, overlays, inspector, teclado, guardar/continuar, pantalla de título y menú) y la **cadena
causal completa** (escasez → competencia → búsqueda → migración en oleada con la causa registrada →
cambio de distribución → relaciones → guardar → reiniciar → consecuencias presentes).

Otras verificaciones ejecutadas: ejecución headless de 2–9 días sin errores, construcción
PyInstaller en Linux y ejecución del binario (headless y captura), capturas automáticas de todas las
vistas, `inspect validate` sobre partidas reales.

## Pendiente / limitaciones

* Sin pruebas en Windows ni en una pantalla física; sin escucha real del audio.
* Los depredadores pueden quedar muy escasos a largo plazo en algunas semillas.
* La simulación abstracta no modela territorios en detalle (solo fuerza agregada) ni rutas.
* La historia de regiones latentes se registra al avanzarlas, por lo que puede llegar con marcas de
  tiempo ligeramente desordenadas (la crónica se ordena por tick al mostrarla).
* El jugador no puede ser cazado (solo expulsado de territorios) por decisión de diseño.
