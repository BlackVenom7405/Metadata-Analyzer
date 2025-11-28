from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "tiff", "webp"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def format_size(bytes_size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(bytes_size)
    for unit in units:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def get_basic_info(image_path: str, img: Image.Image):
    width, height = img.size
    return {
        "file_name": os.path.basename(image_path),
        "format": img.format,
        "mode": img.mode,
        "resolution": f"{width} x {height} px",
        "width": width,
        "height": height,
        "file_size": format_size(os.path.getsize(image_path)),
    }


def get_exif_data(img: Image.Image):
    """
    Extract EXIF data and return a dict with readable tag names.
    Keep raw values here (we sanitize before JSON).
    """
    exif_raw = None
    try:
        exif_raw = img._getexif()
    except Exception:
        exif_raw = None

    if not exif_raw:
        return {}

    exif_data = {}
    for tag_id, value in exif_raw.items():
        tag_name = TAGS.get(tag_id, str(tag_id))
        exif_data[tag_name] = value
    return exif_data


# ------------------ Helpers: convert rationals/bytes/tuples to JSON-safe ------------------ #
def sanitize_for_json(obj):
    """
    Recursively convert object into JSON-serializable types:
      - IFDRational or other objects -> float(obj) when possible
      - bytes -> decoded str
      - tuples/lists -> list of sanitized items
      - dict -> sanitize values
      - fallback -> str(obj)
    """
    # None or already simple types
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj

    # bytes -> decode
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode(errors="ignore")
        except Exception:
            return str(obj)

    # dict -> sanitize values
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}

    # list/tuple -> sanitize each element
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(x) for x in obj]

    # Try converting to float (works for IFDRational)
    try:
        f = float(obj)
        # If float conversion succeeded, return float (but keep ints as ints where appropriate)
        if f.is_integer():
            return int(f)
        return f
    except Exception:
        pass

    # Try to get numerator/denominator attributes (rare)
    try:
        num = getattr(obj, "numerator", None) or getattr(obj, "num", None)
        den = getattr(obj, "denominator", None) or getattr(obj, "den", None)
        if num is not None and den is not None:
            try:
                val = float(num) / float(den)
                if float(val).is_integer():
                    return int(val)
                return float(val)
            except Exception:
                pass
    except Exception:
        pass

    # Fallback: string representation
    try:
        return str(obj)
    except Exception:
        return None


# ------------------ GPS helpers (use sanitized raw values) ------------------ #
def _to_float_rational(r):
    """
    Convert various 'rational-like' objects to float.
    """
    try:
        return float(r)
    except Exception:
        pass

    if isinstance(r, (list, tuple)) and len(r) == 2:
        num, den = r
        return float(num) / float(den)

    num = getattr(r, "numerator", None) or getattr(r, "num", None)
    den = getattr(r, "denominator", None) or getattr(r, "den", None)
    if num is not None and den is not None:
        return float(num) / float(den)

    raise ValueError(f"Cannot convert rational value to float: {repr(r)}")


def _convert_to_degrees(value):
    """
    Convert GPS coordinate parts to decimal degrees.
    value usually is a sequence of three rationals (deg, min, sec),
    but handle other forms gracefully.
    """
    try:
        if isinstance(value, (list, tuple)) and len(value) == 3:
            d = _to_float_rational(value[0])
            m = _to_float_rational(value[1])
            s = _to_float_rational(value[2])
            return d + (m / 60.0) + (s / 3600.0)
        # Single rational-like value
        return _to_float_rational(value)
    except Exception:
        return None


def get_gps_info(exif_data: dict):
    """Extract GPS information in a readable format, if present. Return JSON-safe raw dict."""
    gps_info = exif_data.get("GPSInfo")
    if not gps_info:
        return None

    gps_data_raw = {}
    for key, val in gps_info.items():
        sub_tag = GPSTAGS.get(key, key)
        gps_data_raw[sub_tag] = val  # keep raw for conversion; we'll sanitize later

    # Compute decimal lat/lon if possible
    lat = lon = None
    lat_ref = gps_data_raw.get("GPSLatitudeRef")
    lon_ref = gps_data_raw.get("GPSLongitudeRef")

    # Normalize refs (bytes -> string)
    if isinstance(lat_ref, (bytes, bytearray)):
        try:
            lat_ref = lat_ref.decode(errors="ignore")
        except Exception:
            lat_ref = str(lat_ref)
    if isinstance(lon_ref, (bytes, bytearray)):
        try:
            lon_ref = lon_ref.decode(errors="ignore")
        except Exception:
            lon_ref = str(lon_ref)

    if "GPSLatitude" in gps_data_raw:
        lat = _convert_to_degrees(gps_data_raw["GPSLatitude"])
        if lat is not None and lat_ref and str(lat_ref).upper().startswith("S"):
            lat = -lat

    if "GPSLongitude" in gps_data_raw:
        lon = _convert_to_degrees(gps_data_raw["GPSLongitude"])
        if lon is not None and lon_ref and str(lon_ref).upper().startswith("W"):
            lon = -lon

    # Sanitize the raw gps dict so it is JSON serializable
    gps_data_sanitized = {k: sanitize_for_json(v) for k, v in gps_data_raw.items()}

    return {
        "raw": gps_data_sanitized,
        "latitude": lat,
        "longitude": lon,
    }


# Tags we’ll mark as "sensitive" in the UI
SENSITIVE_TAGS = {
    "GPSInfo",
    "GPSLatitude",
    "GPSLongitude",
    "GPSLatitudeRef",
    "GPSLongitudeRef",
    "DateTime",
    "DateTimeOriginal",
    "DateTimeDigitized",
    "Model",
    "Make",
    "OwnerName",
    "Software",
}


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No file part 'image' in request"}), 400

    file = request.files["image"]

    if file.filename == "":
        return jsonify({"success": False, "error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "File type not allowed. Use jpg, jpeg, png, tiff, or webp.",
                }
            ),
            400,
        )

    # Save file temporarily (use secure filename)
    filename = secure_filename(file.filename)
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)

    try:
        with Image.open(save_path) as img:
            basic_info = get_basic_info(save_path, img)
            exif_data = get_exif_data(img)

            if not exif_data:
                return jsonify(
                    {
                        "success": True,
                        "basic_info": basic_info,
                        "exif": [],
                        "gps": None,
                        "privacy_flags": {
                            "has_gps": False,
                            "has_datetime": False,
                            "has_camera_model": False,
                        },
                        "message": "No EXIF metadata found in this image.",
                    }
                )

            gps = get_gps_info(exif_data)

            exif_list = []
            has_datetime = False
            has_camera_model = False

            for tag, value in exif_data.items():
                # For display we convert bytes / rationals etc. into a string summary
                display_value = value
                if isinstance(display_value, (bytes, bytearray)):
                    try:
                        display_value = display_value.decode(errors="ignore")
                    except Exception:
                        display_value = str(display_value)

                # If it's a list/tuple (e.g. rationals), try to make a readable representation
                if isinstance(display_value, (list, tuple)):
                    try:
                        display_value = sanitize_for_json(display_value)
                    except Exception:
                        display_value = str(display_value)

                # If it is not a simple type, cast to string (safe presentation)
                if not isinstance(display_value, (str, int, float, bool, type(None))):
                    try:
                        display_value = str(display_value)
                    except Exception:
                        display_value = "<unserializable>"

                value_str = str(display_value)
                if len(value_str) > 200:
                    value_str = value_str[:200] + "... (truncated)"

                is_sensitive = tag in SENSITIVE_TAGS

                if tag in ["DateTime", "DateTimeOriginal", "DateTimeDigitized"]:
                    has_datetime = True
                if tag in ["Model", "Make"]:
                    has_camera_model = True

                exif_list.append(
                    {
                        "tag": tag,
                        "value": value_str,
                        "is_sensitive": is_sensitive,
                    }
                )

            privacy_flags = {
                "has_gps": gps is not None
                and gps.get("latitude") is not None
                and gps.get("longitude") is not None,
                "has_datetime": has_datetime,
                "has_camera_model": has_camera_model,
            }

            return jsonify(
                {
                    "success": True,
                    "basic_info": basic_info,
                    "exif": exif_list,
                    "gps": gps,
                    "privacy_flags": privacy_flags,
                    "message": "Metadata analyzed successfully.",
                }
            )
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to analyze image: {e}"}), 500
    finally:
        # remove saved file to avoid buildup (ignore errors)
        try:
            if os.path.exists(save_path):
                os.remove(save_path)
        except Exception:
            pass


if __name__ == "__main__":
    app.run(debug=True)
