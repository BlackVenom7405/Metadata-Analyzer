import os
import sys
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

def format_size(bytes_size: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.2f} TB"


def get_basic_info(image_path: str):
    """Return basic info like format and dimensions."""
    with Image.open(image_path) as img:
        width, height = img.size
        return {
            "File name": os.path.basename(image_path),
            "Format": img.format,
            "Mode": img.mode,
            "Resolution": f"{width} x {height} px"
        }


def get_exif_data(image_path: str):
    """Extract EXIF data and return as a dict with readable tag names."""
    with Image.open(image_path) as img:
        exif_raw = img._getexif()

        if not exif_raw:
            return {}

        exif_data = {}
        for tag_id, value in exif_raw.items():
            tag_name = TAGS.get(tag_id, tag_id)
            exif_data[tag_name] = value
        return exif_data


def get_gps_info(exif_data: dict):
    """Extract GPS information in a readable format, if present."""
    gps_info = exif_data.get("GPSInfo")
    if not gps_info:
        return None

    gps_data = {}
    for key, val in gps_info.items():
        sub_tag = GPSTAGS.get(key, key)
        gps_data[sub_tag] = val

    def _convert_to_degrees(value):
        # value is like ((deg_num, deg_den), (min_num, min_den), (sec_num, sec_den))
        d = value[0][0] / value[0][1]
        m = value[1][0] / value[1][1]
        s = value[2][0] / value[2][1]
        return d + (m / 60.0) + (s / 3600.0)

    lat = lon = None
    if "GPSLatitude" in gps_data and "GPSLatitudeRef" in gps_data:
        lat = _convert_to_degrees(gps_data["GPSLatitude"])
        if gps_data["GPSLatitudeRef"] in ["S", "South"]:
            lat = -lat

    if "GPSLongitude" in gps_data and "GPSLongitudeRef" in gps_data:
        lon = _convert_to_degrees(gps_data["GPSLongitude"])
        if gps_data["GPSLongitudeRef"] in ["W", "West"]:
            lon = -lon

    return {
        "Raw GPS": gps_data,
        "Latitude": lat,
        "Longitude": lon
    }


def print_metadata(image_path: str):
    """Analyze and print all metadata for the given image."""
    if not os.path.isfile(image_path):
        print(f"[ERROR] File not found: {image_path}")
        return

    print("=" * 60)
    print(f"Analyzing: {image_path}")
    print("=" * 60)

    # Basic file info
    file_size = os.path.getsize(image_path)
    print("\n[Basic Info]")
    basic_info = get_basic_info(image_path)
    basic_info["File size"] = format_size(file_size)
    for k, v in basic_info.items():
        print(f"- {k}: {v}")

    # EXIF data
    exif_data = get_exif_data(image_path)
    if not exif_data:
        print("\n[EXIF Metadata]")
        print("No EXIF metadata found.")
        return

    print("\n[EXIF Metadata]")
    for tag, value in exif_data.items():
        # Truncate long values for readability
        value_str = str(value)
        if len(value_str) > 100:
            value_str = value_str[:100] + "... (truncated)"
        print(f"- {tag}: {value_str}")

    # GPS info (if present)
    gps = get_gps_info(exif_data)
    if gps:
        print("\n[GPS Info]")
        print(f"- Latitude:  {gps['Latitude']}")
        print(f"- Longitude: {gps['Longitude']}")
        print(f"- Raw GPS dict has {len(gps['Raw GPS'])} entries")
        print("\n[Privacy Warning]")
        print("This image contains GPS location data. It can reveal where the photo was taken.")
    else:
        print("\n[GPS Info]")
        print("No GPS data found.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python metadata_analyzer.py <image1> [image2 ...]")
        sys.exit(1)

    for image_path in sys.argv[1:]:
        print_metadata(image_path)
        print("\n")


if __name__ == "__main__":
    main()
