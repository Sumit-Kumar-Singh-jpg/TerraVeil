"""Read-only serving for the prepared InSAR map; no telemetry dependencies."""
import re
from pathlib import Path
from flask import Blueprint, current_app, jsonify, request, send_from_directory

insar = Blueprint('insar', __name__, url_prefix='/api/insar')


@insar.get('/<filename>')
def asset(filename):
    if filename != 'manifest.json' and not re.fullmatch(r'terrain-[a-f0-9]{16}\.bin', filename):
        return jsonify(error='Unknown InSAR asset'), 404
    folder = Path(current_app.config.get('INSAR_DATA_DIR', Path(current_app.static_folder)/'insar/jharia'))
    compressed = filename.endswith('.bin') and request.accept_encodings['gzip'] > 0 and (folder/(filename+'.gz')).is_file()
    response = send_from_directory(folder, filename+'.gz' if compressed else filename,
        mimetype='application/json' if filename.endswith('.json') else 'application/octet-stream',
        max_age=0 if filename.endswith('.json') else 31536000)
    if compressed:
        response.headers['Content-Encoding'] = 'gzip'
    response.headers['Vary'] = 'Accept-Encoding'
    if filename == 'manifest.json':
        response.headers['Cache-Control'] = 'no-store'
    return response
