"""Console messages shown before the TUI opens (login, logs) and after it closes."""
from ..errors import AppError, error_label


class Terminal:
    def notify(self, event, **data):
        if event == "logs-ready":
            print(f"Diagnostico: {data['path']}")
        elif event == "logs-unavailable":
            print("No se pudo abrir el archivo de diagnostico.")
        elif event == "welcome":
            print("NONETO CLI")
        elif event == "logout":
            print("Sesion local eliminada.")
        elif event == "mpv-installing":
            print("Instalando mpv (si falta)...")
        elif event == "mpv-ready":
            print(f"mpv listo: {data['path']}")
        elif event == "bye":
            print("\nHasta luego.")
        elif event == "error":
            error = data["error"]
            if isinstance(error, AppError):
                print(f"\n{error}")
            else:
                print(f"\nNo se pudo completar la operacion ({error_label(error)}).")
                print("Comprueba tu conexion y tu cuenta. Para iniciar sesion otra vez: noneto --logout")
        elif event == "session-restored":
            print("Sesion recuperada.")
        elif event == "session-invalid":
            print("No se pudo recuperar la sesion. Inicia sesion nuevamente.")
        elif event == "authorize":
            print(f"\nAutoriza Noneto CLI desde tu navegador:\n{data['url']}")
            print("Esperando autorizacion... (Ctrl+C para cancelar)")
        elif event == "session-started":
            print("Sesion iniciada.")
