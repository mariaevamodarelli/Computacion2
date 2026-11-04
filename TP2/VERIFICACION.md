# Verificación de la versión final

- 13 pruebas automatizadas aprobadas desde este TP2.
- Mediciones repetidas con la implementación de hash en bloques de 50.000 iteraciones.
- JSON, informe y tablas del README actualizados automáticamente.
- Prueba HTTP real de cancelación de hash y apagado con SIGTERM y SIGINT: cierre completo y volcado JSON verificados.
- Configuración de Docker Compose válida.
- git diff --check sin errores.

## Comandos para repetir

```sh
python -m unittest discover -s tests -v
python -m benchmarks.medir
```

## Docker: arranque comprobado por el usuario

El usuario ejecutó Docker Compose en su terminal y confirmó una respuesta JSON
correcta de GET /estadisticas desde el host. El arranque del contenedor y su
accesibilidad quedaron comprobados; las pruebas de señales y cancelación se
ejecutaron directamente con Python, fuera del contenedor.

Desde TP2:

```sh
docker compose up --build -d
curl --fail http://localhost:8000/estadisticas
docker compose logs --tail=30 api
```

La sección «Lo que aprendí» se incorporó al README. La visibilidad pública del
repositorio no se verificó.
