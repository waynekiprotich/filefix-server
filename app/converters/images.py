from PIL import Image, ImageOps


def convert_image(source, output, options, progress):
    progress(10, 'Decoding image')
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        if output.suffix in ('.jpg', '.pdf'):
            rgba = image.convert('RGBA')
            result = Image.new('RGB', rgba.size, 'white')
            result.paste(rgba, mask=rgba.getchannel('A'))
        else:
            result = image.convert('RGBA')
        result.save(output, quality=95)
    return output
