import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import numpy as np

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(layout="wide", page_title="Inventario B&M")
st.title("Sistema de Gestión de Inventario B&M (Google Sheets)")

# --- CONFIGURACIÓN GOOGLE SHEETS ---
# Nombre exacto de tu hoja de cálculo en Google Drive
GOOGLE_SHEET_TITLE = "Inventario B&M" 

# Nombres de las pestañas
INVENTARIO_WS = 'inventario'
STOCK_MINIMO_WS = 'stock_minimo'
MOVIMIENTOS_WS = 'movimientos'

# Cabeceras
inventario_headers = ["codigo", "nombre", "marca", "cantidad", "fecha_vencimiento", "precio_costo", "precio_venta"]
stock_minimo_headers = ['codigo', 'stock_min']
movimientos_headers = ["timestamp", "tipo", "codigo", "nombre", "cantidad", "fecha_vencimiento", "precio_costo", "precio_venta"]

# Variables globales en memoria (usadas por el resto del script)
inventario = {}
stock_minimo = {}
movimientos = []

# --- CONEXIÓN Y FUNCIONES AUXILIARES ---

@st.cache_resource(ttl=3600)
def obtener_conexion():
    """Conecta con Google Sheets usando st.secrets"""
    try:
        # Crea un diccionario con las credenciales desde secrets
        credentials = dict(st.secrets["gcp_service_account"])
        
        # Autentica
        gc = gspread.service_account_from_dict(credentials)
        
        # Abre la hoja
        sh = gc.open(GOOGLE_SHEET_TITLE)
        return sh
    except Exception as e:
        st.error(f"Error de conexión: {e}. Verifica que el bot tenga permiso de edición en la hoja '{GOOGLE_SHEET_TITLE}' y que los secrets estén bien pegados.")
        st.stop()

def check_worksheets(sh):
    """Asegura que las pestañas existan y tengan headers"""
    try:
        titulos_actuales = [ws.title for ws in sh.worksheets()]
        
        if INVENTARIO_WS not in titulos_actuales:
            ws = sh.add_worksheet(title=INVENTARIO_WS, rows=100, cols=10)
            ws.append_row(inventario_headers)
            
        if STOCK_MINIMO_WS not in titulos_actuales:
            ws = sh.add_worksheet(title=STOCK_MINIMO_WS, rows=100, cols=5)
            ws.append_row(stock_minimo_headers)
            
        if MOVIMIENTOS_WS not in titulos_actuales:
            ws = sh.add_worksheet(title=MOVIMIENTOS_WS, rows=100, cols=10)
            ws.append_row(movimientos_headers)
    except Exception as e:
        st.error(f"Error verificando pestañas: {e}")

def normalizar_fecha(fecha_obj) -> str:
    if not fecha_obj: return ""
    try:
        if isinstance(fecha_obj, str):
            fecha_obj = fecha_obj.strip()
            if ' ' in fecha_obj: fecha_obj = fecha_obj.split(' ')[0]
            if 'T' in fecha_obj: fecha_obj = fecha_obj.split('T')[0]
            return fecha_obj
        
        if hasattr(fecha_obj, 'strftime'):
            return fecha_obj.strftime("%Y-%m-%d")
        
        return str(fecha_obj).split(' ')[0]
    except: return ""

def _convertir_a_numero(valor, por_defecto=0):
    if valor is None or valor == '': return por_defecto
    try: return int(valor)
    except (ValueError, TypeError):
        try: return float(valor)
        except (ValueError, TypeError): return por_defecto

def stock_total(codigo: str) -> int:
    return sum(l["cantidad"] for l in inventario.get(codigo, []))

def ordenar_lotes_fifo(lotes):
    def clave(l):
        fv = l.get("fecha_vencimiento", "")
        if not fv:
            return (datetime.max, 1)
        try:
            return (datetime.fromisoformat(fv), 0)
        except Exception:
            return (datetime.max, 1)
    return sorted(lotes, key=clave)

def _escribir_sheet(ws_name, headers, datos):
    """Sobreescribe una pestaña completa con nuevos datos"""
    try:
        sh = obtener_conexion()
        ws = sh.worksheet(ws_name)
        ws.clear()
        ws.append_row(headers)
        if datos:
            datos_limpios = []
            for fila in datos:
                # Aseguramos que las listas tengan el mismo tamaño que el header
                fila_expandida = list(fila) + ["" for _ in range(len(headers) - len(fila))]
                fila_str = [str(celda) if celda is not None else "" for celda in fila_expandida[:len(headers)]]
                datos_limpios.append(fila_str)

            ws.append_rows(datos_limpios, value_input_option='USER_ENTERED')
    except Exception as e:
        st.error(f"Error guardando en {ws_name}: {e}")

# --- LOGICA DE NEGOCIO ---

def cargar_todo():
    """Carga datos desde Sheets a memoria. Se ejecuta en cada run de Streamlit."""
    inventario.clear()
    stock_minimo.clear()
    movimientos.clear()
    
    sh = obtener_conexion()
    check_worksheets(sh)
    
    # 1. Cargar Inventario
    try:
        ws_inv = sh.worksheet(INVENTARIO_WS)
        vals_inv = ws_inv.get_all_values()
        if len(vals_inv) > 1:
            for fila in vals_inv[1:]: # Saltar header
                fila += [""] * (len(inventario_headers) - len(fila))
                codigo, nombre, marca, cant, fv, pc, pv = fila[:len(inventario_headers)]
                
                if not codigo: continue
                
                lote = {
                    'nombre': nombre,
                    'marca': marca,
                    'cantidad': _convertir_a_numero(cant),
                    'fecha_vencimiento': normalizar_fecha(fv),
                    'precio_costo': _convertir_a_numero(pc),
                    'precio_venta': _convertir_a_numero(pv)
                }
                
                if codigo not in inventario: inventario[codigo] = []
                inventario[codigo].append(lote)
    except Exception as e: st.error(f"Error leyendo inventario: {e}")

    # 2. Cargar Stock Minimo
    try:
        ws_min = sh.worksheet(STOCK_MINIMO_WS)
        vals_min = ws_min.get_all_values()
        if len(vals_min) > 1:
            for fila in vals_min[1:]:
                if fila and fila[0]:
                    stock_minimo[fila[0]] = _convertir_a_numero(fila[1] if len(fila)>1 else 0)
    except Exception as e: st.error(f"Error leyendo stock minimo: {e}")

    # 3. Cargar Movimientos
    try:
        ws_mov = sh.worksheet(MOVIMIENTOS_WS)
        vals_mov = ws_mov.get_all_values()
        if len(vals_mov) > 1:
            # Los movimientos se cargan como lista de listas
            for fila in vals_mov[1:]:
                movimientos.append(fila[:len(movimientos_headers)])
    except Exception as e: st.error(f"Error leyendo movimientos: {e}")
    
    st.session_state['data_loaded'] = True

# Funciones de guardado
def guardar_inventario():
    filas = []
    for codigo, lotes in inventario.items():
        for d in lotes:
            filas.append([
                codigo, d.get('nombre',""), d.get('marca',""), d.get('cantidad',0),
                d.get('fecha_vencimiento',""), d.get('precio_costo',0), d.get('precio_venta',0)
            ])
    _escribir_sheet(INVENTARIO_WS, inventario_headers, filas)

def guardar_stock_minimo():
    filas = [[k, v] for k, v in stock_minimo.items()]
    _escribir_sheet(STOCK_MINIMO_WS, stock_minimo_headers, filas)

def registrar_movimiento(tipo, codigo, nombre, cantidad, fecha_vencimiento, precio_costo, precio_venta):
    nueva_fila = [
        datetime.now().isoformat(timespec="seconds"),
        tipo,
        str(codigo),
        nombre,
        cantidad,
        fecha_vencimiento or "",
        precio_costo if precio_costo is not None else 0,
        precio_venta if precio_venta is not None else 0,
    ]
    # Optimización: Append directo en lugar de reescribir todo
    try:
        sh = obtener_conexion()
        ws = sh.worksheet(MOVIMIENTOS_WS)
        # Convertir a string para evitar errores
        fila_str = [str(x) for x in nueva_fila]
        ws.append_row(fila_str)
        movimientos.append(nueva_fila) # Actualizar local también
    except Exception as e:
        st.error(f"Error registrando movimiento: {e}")

# --- INICIALIZACIÓN ---
if 'data_loaded' not in st.session_state:
    cargar_todo()
else:
    # Recargar datos en cada run para reflejar cambios
    cargar_todo()

# --- INTERFAZ STREAMLIT ---
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["Registrar Entrada", "Registrar Salida", "Mostrar Inventario", "Reporte de Movimientos", "Reporte de Niveles de Stock", "Reporte de Alertas de Vencimiento"])

with tab1:
    st.subheader("Registro de Entradas: ")
    
    if 'reset_counter' not in st.session_state:
        st.session_state.reset_counter = 0

    input_key = f"entrada_input_{st.session_state.reset_counter}"

    entrada = st.text_input("Escanee el código o esciba 'buscar': ", key=input_key)
    
    codigo_seleccionado = None
    
    if entrada:
        if entrada.lower() == 'buscar':
            productos_lista = []
            for codigo, lotes in inventario.items():
                if lotes:
                    base = lotes[0]
                    nombre = base.get('nombre') or 'N/A'
                    marca = base.get('marca') or 'N/A'
                    productos_lista.append((codigo, nombre, marca))
                
            productos_lista.sort(key=lambda x: x[1])

            opciones = [f"{i+1}) {nombre} - {marca} (Código: {codigo})" 
                                for i, (codigo, nombre, marca) in enumerate(productos_lista)]
            
            opciones.insert(0, "Cancelar")
            seleccion = st.selectbox("Seleccione un producto", opciones)

            if seleccion == 'Cancelar':
                codigo_seleccionado = None
            else:
                idx = opciones.index(seleccion) - 1
                codigo_seleccionado = productos_lista[idx][0]
                st.success(f"Se seleleccionó: {productos_lista[idx][1]} (Codigo: {codigo_seleccionado})")
        else:
            codigo_seleccionado = entrada

    if codigo_seleccionado:
        es_nuevo = codigo_seleccionado not in inventario
        no_tiene_min = codigo_seleccionado not in stock_minimo or stock_minimo[codigo_seleccionado] is None

        if es_nuevo:
            st.info(f"El código {codigo_seleccionado} no se encuentra en el inventario. Se creará un nuevo producto")
        else:
            base = inventario[codigo_seleccionado][0]
            st.success(f"Producto existente: {base.get('nombre')} ({base.get('marca')})")
        
        with st.form("form_entrada", clear_on_submit = True):
            nombre_def = '' if es_nuevo else inventario[codigo_seleccionado][0].get('nombre', '')
            marca_def = '' if es_nuevo else inventario[codigo_seleccionado][0].get('marca', '')
            pc_def = 0 if es_nuevo else inventario[codigo_seleccionado][0].get('precio_costo', 0)
            pv_def = 0 if es_nuevo else inventario[codigo_seleccionado][0].get('precio_venta', 0)
            cant_min_def = 0 if no_tiene_min else stock_minimo[codigo_seleccionado]
            
            nombre = st.text_input("Nombre", value = nombre_def, disabled = not es_nuevo)
            marca = st.text_input("Marca", value = marca_def, disabled = not es_nuevo)
            precio_costo = st.number_input("Precio de Costo", min_value = 0, value = int(pc_def), disabled = not es_nuevo)
            precio_venta = st.number_input("Precio de Venta", min_value = 0, value = int(pv_def), disabled = not es_nuevo)

            cantidad = st.number_input("Cantidad a ingresar", min_value = 0, value = 1, step = 1)
            cant_min = st.number_input("Cantidad mínima", min_value = 0, value = int(cant_min_def))
            aplica_vencimiento = st.checkbox("¿El producto tiene fecha de vencimiento?", value = True)
            fecha_vencimiento = st.date_input("Fecha Vencimiento", value = datetime.now().date())

            submitted = st.form_submit_button("Guardar")

            if submitted:
                fv = normalizar_fecha(fecha_vencimiento) if aplica_vencimiento else ""

                if es_nuevo:
                    lote = {
                        'nombre': nombre,
                        'marca': marca,
                        'cantidad': cantidad,
                        'fecha_vencimiento': fv,
                        'precio_costo': precio_costo,
                        'precio_venta': precio_venta
                    }

                    inventario[codigo_seleccionado] = [lote]
                    stock_minimo[codigo_seleccionado] = cant_min
                    mensaje = f'Producto {nombre} creado con éxito'
                else:
                    lotes = inventario[codigo_seleccionado]
                    lote_existente = next((l for l in lotes if l.get("fecha_vencimiento", "") == fv), None)

                    if lote_existente:
                        lote_existente['cantidad'] += cantidad
                        mensaje = f"Se agregaron {cantidad} unidades al lote existente ({fv})"
                    else:
                        lotes.append({
                            'nombre': nombre,
                            'marca': marca,
                            'cantidad': cantidad,
                            'fecha_vencimiento': fv,
                            'precio_costo': precio_costo,
                            'precio_venta': precio_venta
                        })
                        stock_minimo[codigo_seleccionado] = cant_min
                        mensaje = f"Se creó un nuevo lote con {cantidad} unidades ({fv})"

                guardar_inventario()
                guardar_stock_minimo()
                registrar_movimiento("entrada", codigo_seleccionado, nombre, cantidad, fv, precio_costo, precio_venta)
                st.success(mensaje) 
                st.session_state.reset_counter += 1
                st.rerun()

with tab2:

    if "lista" not in st.session_state:
        st.session_state.lista = {}

    def procesar_codigo_escaneado():
        codigo_sin_procesar = st.session_state.codigo
        if not codigo_sin_procesar:
            return

        cantidad = 1
        codigo_producto = ""

        if "*" in codigo_sin_procesar:
            try:
                partes = codigo_sin_procesar.split("*", 1)
                cantidad = _convertir_a_numero(partes[0].strip(), por_defecto=1)
                codigo_producto = partes[1].strip()
            except:
                codigo_producto = codigo_sin_procesar.strip()
        else:
            codigo_producto = codigo_sin_procesar.strip()
            cantidad = 1
        
        if codigo_producto not in inventario:
            st.toast(f"El {codigo_producto} no existe")
            st.session_state.codigo = ""
            return

        stock_disp = stock_total(codigo_producto)
        en_lista = st.session_state.lista.get(codigo_producto, 0)
        
        if (en_lista + cantidad) > stock_disp:
            st.toast(f"Stock insuficiente. Disponible: {stock_disp}")
        else:
            if codigo_producto in st.session_state.lista:
                st.session_state.lista[codigo_producto] += cantidad
            else:
                st.session_state.lista[codigo_producto] = cantidad
            st.toast(f"Agregado: {codigo_producto}")

        st.session_state.codigo = ""

    st.subheader("Registro de Salidas")
    st.text_input("Escanee el código del producto (Si son varios, ej: 5*7806505055391)", key="codigo", on_change=procesar_codigo_escaneado)

    st.divider()
    st.subheader("Lista Actual de Productos")

    if not st.session_state.lista:
        st.info("No hay productos en la lista")
    else:
        total_productos = 0
        for codigo, cant_lista in st.session_state.lista.items():
            if codigo in inventario:
                nombre = inventario[codigo][0]['nombre']
                st.write(f"- **{nombre}**: {cant_lista} unidades")
                total_productos += cant_lista
            
        st.subheader(f"Total de Productos: {total_productos}")

        if st.button("Registrar Salidas (Confirmar)"):
            for codigo_prod, cantidad_sacar in st.session_state.lista.items():
                if codigo_prod not in inventario: continue

                lotes_a_modificar = ordenar_lotes_fifo(inventario[codigo_prod])
                restante = cantidad_sacar
                nombre_prod = lotes_a_modificar[0]['nombre']

                lotes_finales = []
                for l in lotes_a_modificar:
                    if restante > 0:
                        toma = min(l['cantidad'], restante)
                        l['cantidad'] -= toma
                        restante -= toma
                        
                        registrar_movimiento("salida", codigo_prod, nombre_prod, toma, l.get('fecha_vencimiento',''), l.get('precio_costo'), l.get('precio_venta'))
                    
                    if l['cantidad'] > 0:
                        lotes_finales.append(l)
                
                if not lotes_finales:
                    if codigo_prod in inventario:
                        del inventario[codigo_prod]
                else:
                    inventario[codigo_prod] = lotes_finales
            
            guardar_inventario()
            st.session_state.lista = {}
            st.success("Salidas registradas correctamente")
            st.rerun() 

# === TAB 3: MOSTRAR INVENTARIO ===
with tab3:
    st.subheader("Inventario Completo")
    
    # Lógica para cargar el DataFrame directamente desde Google Sheets (REEMPLAZANDO pd.read_excel)
    try:
        sh = obtener_conexion()
        ws_inv = sh.worksheet(INVENTARIO_WS)
        data = ws_inv.get_all_values()
        
        if len(data) > 1:
            df_inv = pd.DataFrame(data[1:], columns=data[0])
            # Convertir cantidad a numérico
            df_inv['cantidad'] = df_inv['cantidad'].apply(_convertir_a_numero)
        else:
            st.warning("No hay datos en la hoja de inventario.")
            df_inv = pd.DataFrame(columns=inventario_headers)

        busqueda = st.text_input("🔍 Buscar producto (Nombre, Marca o Código):", key="search_inv")

        if busqueda:
            busqueda_lower = busqueda.lower()
            filtro = (df_inv['codigo'].astype(str).str.contains(busqueda_lower, case=False, na=False)) | \
                     (df_inv['nombre'].astype(str).str.contains(busqueda_lower, case=False, na=False)) | \
                     (df_inv['marca'].astype(str).str.contains(busqueda_lower, case=False, na=False))
            df_filtrado = df_inv[filtro]
        else:
            df_filtrado = df_inv

        
        st.dataframe(
            df_filtrado, 
            width="stretch", 
            height=500,
            hide_index=True,
            column_config={
                'codigo': st.column_config.TextColumn("Código"),
                'nombre': st.column_config.TextColumn("Nombre"),
                'marca': st.column_config.TextColumn("Marca"),
                "fecha_vencimiento": st.column_config.TextColumn("Fecha de Vencimiento"),
                "cantidad": st.column_config.NumberColumn("Stock"),
                "precio_costo": st.column_config.NumberColumn("P. Costo", format="$%d"),
                "precio_venta": st.column_config.NumberColumn("P. Venta", format="$%d"),
            }
        )
        
    except Exception as e:
        st.error(f"Error al cargar la hoja de inventario desde Google Sheets: {e}")

# === TAB 4: REPORTE MOVIMIENTOS ===
with tab4:
    st.subheader("Reporte de Movimientos: ")
    col_izq, col_der = st.columns(2)

    with col_izq:
        productos_lista = []
        for codigo, lotes in inventario.items():
            if lotes:
                base = lotes[0]
                productos_lista.append((codigo, base.get('nombre') or 'N/A', base.get('marca') or 'N/A'))
        productos_lista.sort(key=lambda x: x[1])

        opciones = [f"{i+1}) {nombre} - {marca} (Código: {codigo})" for i, (codigo, nombre, marca) in enumerate(productos_lista)]
        opciones.insert(0, "Cancelar")
        opciones.insert(1, 'Todos los productos')
        seleccion = st.selectbox("Seleccione un producto", opciones, key="mov_prod_sel")

        codigo_seleccionado = None
        if seleccion == 'Cancelar':
            pass
        elif seleccion == 'Todos los productos':
            pass
        else:
            idx = opciones.index(seleccion) - 2
            codigo_seleccionado = productos_lista[idx][0]

        fecha_inicio = st.date_input("Ingrese la fecha de inicio: ", key="mov_f_ini")
        fecha_fin = st.date_input("Ingrese la fecha de fin: ", key="mov_f_fin")

        tipo_movimiento = st.multiselect("Seleccione el tipo de movimiento", ["entrada", "salida"], key="tipo_movimiento")

    if col_izq.button("Mostrar Movimientos: "):
        if not movimientos:
            col_izq.info("No hay movimientos registrados.")
        else:
            try:
                # Usar la lista global de movimientos cargada desde Sheets
                df = pd.DataFrame(movimientos, columns=movimientos_headers)
            
                df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
                
                fecha_inicio = pd.to_datetime(fecha_inicio)
                fecha_fin = pd.to_datetime(fecha_fin) + pd.Timedelta(days=1)
                
                df_filtrado = df.loc[(df["timestamp"] >= fecha_inicio) & (df["timestamp"] < fecha_fin)].copy()
                df_filtrado['cantidad'] = df_filtrado['cantidad'].apply(_convertir_a_numero)

                if tipo_movimiento:
                    df_filtrado = df_filtrado[df_filtrado['tipo'].isin(tipo_movimiento)]

                if codigo_seleccionado is not None:
                    df_filtrado = df_filtrado[df_filtrado['codigo'] == codigo_seleccionado]

                df_filtrado['fecha'] = df_filtrado['timestamp'].dt.date
                df_filtrado['hora'] = df_filtrado['timestamp'].dt.time

                columnas_finales = ["fecha", 'hora', "tipo", "nombre", "cantidad"]
                columnas_finales = [c for c in columnas_finales if c in df_filtrado.columns]

                df_filtrado = df_filtrado.rename(columns = {'fecha': 'Fecha', 'hora': "Hora", "tipo": "Tipo", "nombre": "Nombre", "cantidad": "Cantidad"})
                
                nuevos_nombres = ["Fecha", 'Hora', "Tipo", "Nombre", "Cantidad"]
                nuevos_nombres = [c for c in nuevos_nombres if c in df_filtrado.columns]


                if 'entrada' in tipo_movimiento and 'salida' in tipo_movimiento:
                    df_e = df_filtrado[df_filtrado['Tipo'] == 'entrada']
                    df_s = df_filtrado[df_filtrado['Tipo'] == 'salida']

                    if not df_e.empty:
                        fig1, ax1 = plt.subplots(figsize=(10, 5))
                        ax1.scatter(df_e["timestamp"], df_e["Cantidad"], alpha=0.7, edgecolor="k")
                        ax1.set_xlabel("Fecha / Hora")
                        ax1.set_ylabel("Cantidad")
                        ax1.set_title(f"Entradas de {productos_lista[idx][1] if codigo_seleccionado else 'Varios'}")
                        ax1.grid(True)
                        col_der.pyplot(fig1)
                    
                    if not df_s.empty:
                        fig2, ax2 = plt.subplots(figsize=(10, 5))
                        ax2.scatter(df_s["timestamp"], df_s["Cantidad"], alpha=0.7, edgecolor="k")
                        ax2.set_xlabel("Fecha / Hora")
                        ax2.set_ylabel("Cantidad")
                        ax2.set_title(f"Salidas de {productos_lista[idx][1] if codigo_seleccionado else 'Varios'}")
                        ax2.grid(True)
                        col_der.pyplot(fig2)

                elif 'salida' in tipo_movimiento:
                    df_s = df_filtrado[df_filtrado['Tipo'] == 'salida']
                    if not df_s.empty:
                        fig, ax = plt.subplots(figsize=(10, 5))
                        ax.scatter(df_s["timestamp"], df_s["Cantidad"], alpha=0.7, edgecolor="k")
                        ax.set_xlabel("Fecha / Hora")
                        ax.set_ylabel("Cantidad")
                        ax.set_title(f"Salidas de {productos_lista[idx][1] if codigo_seleccionado else 'Varios'}")
                        ax.grid(True)
                        col_der.pyplot(fig)
                else: # Incluye solo 'entrada' o vacío
                    df_e = df_filtrado[df_filtrado['Tipo'] == 'entrada']
                    if not df_e.empty:
                        fig, ax = plt.subplots(figsize=(10, 5))
                        ax.scatter(df_e["timestamp"], df_e["Cantidad"], alpha=0.7, edgecolor="k")
                        ax.set_xlabel("Fecha / Hora")
                        ax.set_ylabel("Cantidad")
                        ax.set_title(f"Entradas de {productos_lista[idx][1] if codigo_seleccionado else 'Varios'}")
                        ax.grid(True)
                        col_der.pyplot(fig)

                if codigo_seleccionado is not None:
                    col_izq.markdown("### Tabla de Movimientos")
                    col_izq.table(df_filtrado[nuevos_nombres])
                else:
                    col_der.markdown("### Tabla de Movimientos")
                    col_der.table(df_filtrado[nuevos_nombres])
            except Exception as e:
                col_izq.error(f"Error al procesar movimientos: {e}")

# === TAB 5: STOCK ===
with tab5:
    st.subheader("Reporte de Niveles de Stock: ")

    # Construir DF de inventario desde la memoria
    data_rows = []
    for c, lotes in inventario.items():
        if lotes:
            base = lotes[0]
            data_rows.append({
                'codigo': c,
                'nombre': base.get('nombre'),
                'stock_total_calc': stock_total(c)
            })
    df_inv_sin_lotes = pd.DataFrame(data_rows)
    
    # Construir DF de stock mínimo desde la memoria
    df_stock_min = pd.DataFrame(list(stock_minimo.items()), columns=['codigo', 'stock_min'])
    df_stock_min['stock_min'] = df_stock_min['stock_min'].apply(_convertir_a_numero)
    
    if not df_inv_sin_lotes.empty:
        df_reporte = pd.merge(
            df_stock_min,
            df_inv_sin_lotes[['codigo', 'nombre', 'stock_total_calc']],
            on = 'codigo',
            how = 'outer'
        )

        df_reporte['stock_total_calc'] = df_reporte['stock_total_calc'].fillna(0).astype(int)
        df_reporte['stock_min'] = df_reporte['stock_min'].fillna(0).astype(int)
        df_reporte['nombre'] = df_reporte['nombre'].fillna('Sin nombre')
        df_reporte = df_reporte.dropna(subset=['codigo']) # Limpiar códigos nulos

        def semaforo(fila):
            total = fila['stock_total_calc']
            minimo = fila['stock_min']

            if total <= minimo:
                return "🔴 Crítico"
            elif total <= 1.5*minimo:
                return "🟡 Advertencia"
            else:
                return "🟢 Óptimo"

        df_reporte['Estado'] = df_reporte.apply(semaforo, axis = 1)
        df_reporte = df_reporte.sort_values(by=['Estado', 'stock_total_calc'], ascending=[False, True])


        busqueda = st.text_input("🔍 Buscar producto (Código):", key="search_stock_min")

        if busqueda:
            codigo = df_reporte['codigo'].astype(str).str.contains(busqueda, case=False, na=False)
            df_reporte = df_reporte[codigo]
        
        # Eliminar duplicados de códigos que podrían generarse en el merge (solo mantener uno)
        df_reporte = df_reporte.drop_duplicates(subset=['codigo'])

        st.dataframe(
            df_reporte, 
            width="stretch", 
            height="stretch",
            hide_index=True,
            column_config={
                'codigo': st.column_config.TextColumn("Código"),
                'nombre': st.column_config.TextColumn("Nombre"),
                "stock_min": st.column_config.NumberColumn("Stock Mínimo"),
                'stock_total_calc': st.column_config.NumberColumn("Stock Actual"),
                'Estado': st.column_config.TextColumn("Estado")
            },
            column_order=("codigo", 'nombre', 'stock_min', 'stock_total_calc', 'Estado')
        )
    else:
        st.warning("No hay productos en el inventario para reportar niveles de stock.")


# === TAB 6: VENCIMIENTOS ===
with tab6:
    st.subheader("Reporte de Alertas de Vencimiento: ")
    
    col_v1, col_v2, col_v3 = st.columns(3)
    alerta_critica = col_v1.slider("Días Críticos (🔴)", 0, 130, 3)
    alerta_adv = col_v2.slider("Días Advertencia (🟡)", 0, 130, 7)
    alerta_preventiva = col_v3.slider("Días Preventivos (🟠)", 0, 130, 12)

    hoy = datetime.now().date()

    limite_crit = hoy + timedelta(days=alerta_critica)
    limite_adv = hoy + timedelta(days=alerta_adv)
    limite_prev = hoy + timedelta(days=alerta_preventiva)

    alertas = []

    for codigo, lotes in inventario.items():
        for lote in lotes:
            fv = lote.get("fecha_vencimiento")
            estado = None
            if fv:
                try:
                    fv_date = datetime.strptime(fv, "%Y-%m-%d").date()
                    dias_restantes = (fv_date - hoy).days

                    if dias_restantes < 0:
                        estado = 'Vencido ❌'
                    elif dias_restantes <= alerta_critica:
                        estado = 'Alerta Crítica 🔴'
                    elif  dias_restantes <= alerta_adv:
                        estado = 'Alerta de Advertencia 🟡'
                    elif dias_restantes <= alerta_preventiva:
                        estado = 'Alerta Preventiva 🟠'

                    if estado:
                        alertas.append({
                            'Estado': estado,
                            'Fecha de Vencimiento': fv,
                            'Días restantes': dias_restantes,
                            'Nombre': lote['nombre'],
                            'Cantidad del lote': lote['cantidad'],
                            'Código': codigo
                        })
                except:
                    pass


    if alertas:
        df_alertas = pd.DataFrame(alertas).sort_values(by='Días restantes')
        st.dataframe(df_alertas, width='stretch', hide_index=True,
                     column_order=("Código", 'Nombre', 'Cantidad del lote', 'Fecha de Vencimiento', 'Días restantes', 'Estado'))
    else:
        st.write("No hay productos para los rangos seleccionados.")
