"""Conservative card ROI for portrait photos with neutral table backgrounds."""
from PIL import ImageChops, ImageFilter


def crop_card_background(pixels):
    ratio = pixels.width / pixels.height
    if 1.3 <= ratio <= 1.9:
        return pixels, None
    red, green, blue = pixels.convert("RGB").split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    saturation = ImageChops.subtract(maximum, minimum)
    mask = saturation.point(lambda value: 255 if value > 30 else 0).filter(ImageFilter.MedianFilter(9))
    box = mask.getbbox()
    if not box:
        return pixels, None
    left, top, right, bottom = box
    width, height = right-left, bottom-top
    area = width*height / (pixels.width*pixels.height)
    if width < 200 or height < 100 or not 1.3 <= width/height <= 1.9 or not .08 <= area <= .88:
        return pixels, None
    padding = max(5, round(width*.012))
    box = (max(0,left-padding),max(0,top-padding),min(pixels.width,right+padding),min(pixels.height,bottom+padding))
    return pixels.crop(box), {"method":"color_rectangle_on_neutral_background","box":list(box)}
