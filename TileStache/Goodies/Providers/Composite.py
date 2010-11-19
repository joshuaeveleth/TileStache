""" Layered, composite rendering for TileStache.

NOTE: This code is currently in heavy progress. I'm finishing the addition
of the new JSON style of layer configuration, while the original XML form
is *deprecated* and will be removed in the future TileStache 2.0.

The Composite Provider provides a Photoshop-like rendering pipeline, making it
possible to use the output of other configured tile layers as layers or masks
to create a combined output. Composite is modeled on Lars Ahlzen's TopOSM.

The "stack" configuration parameter describes a layer or stack of layers that
can be combined to create output. A simple stack that merely outputs a single
color orange tile looks like this:

    {"color" "#ff9900"}

Other layers in the current TileStache configuration can be reference by name,
as in this example stack that simply echoes another layer:

    {"src": "layer-name"}

Layers can also be used as masks, as in this example that uses one layer
to mask another layer:

    {"mask": "layer-name", "src": "other-layer"}

Many combinations of "src", "mask", and "color" can be used together, but it's
an error to provide all three.

Finally, the stacking feature allows layers to combined in more complex ways.
This example stack combines a background color and foreground layer:

    [
      {"color": "#ff9900"},
      {"src": "layer-name"}
    ]

Stacks can be nested as well, such as this combination of two background layers
and two foreground layers:

    [
      [
        {"color"" "#0066ff"},
        {"src": "continents"}
      ],
      [
        {"src": "streets"},
        {"src": "labels"}
      ]
    ]

A complete example configuration might look like this:

    {
      "cache":
      {
        "name": "Test"
      },
      "layers": 
      {
        "base":
        {
          "provider": {"name": "mapnik", "mapfile": "mapnik-base.xml"}
        },
        "halos":
        {
          "provider": {"name": "mapnik", "mapfile": "mapnik-halos.xml"},
          "metatile": {"buffer": 128}
        },
        "outlines":
        {
          "provider": {"name": "mapnik", "mapfile": "mapnik-outlines.xml"},
          "metatile": {"buffer": 16}
        },
        "streets":
        {
          "provider": {"name": "mapnik", "mapfile": "mapnik-streets.xml"},
          "metatile": {"buffer": 128}
        },
        "composite":
        {
          "provider":
          {
            "class": "TileStache.Goodies.Providers.Composite.Provider",
            "kwargs":
            {
              "stack":
              [
                {"src": "base"},
                [
                  {"src": "outlines", "mask": "halos"},
                  {"src": "streets"}
                ]
              ]
            }
          }
        }
      }
    }

It's also possible to provide an equivalent "stackfile" argument that refers to
an XML file, but this feature is *deprecated* and will be removed in the future
release of TileStache 2.0.

Corresponding example stackfile XML:

  <?xml version="1.0"?>
  <stack>
    <layer src="base" />
  
    <stack>
      <layer src="outlines">
        <mask src="halos" />
      </layer>
      <layer src="streets" />
    </stack>
  </stack>

Note that each layer in this file refers to a TileStache layer by name.
This complete example can be found in the included examples directory.
"""

import sys

from json import loads as jsonload
from urllib import urlopen
from urlparse import urljoin
from os.path import join as pathjoin
from xml.dom.minidom import parse as parseXML
from StringIO import StringIO

import sympy
import numpy
import PIL.Image
import TileStache

from TileStache.Core import KnownUnknown

class Provider:
    """ Provides a Photoshop-like rendering pipeline, making it possible to use
        the output of other configured tile layers as layers or masks to create
        a combined output.
    """
    def __init__(self, layer, stack=None, stackfile=None):
        """ Make a new Composite.Provider.
            
            Arguments:
            
              layer:
                The current TileStache.Core.Layer
                
              stack:
                A list or dictionary with configuration for the image stack, parsed
                by build_stack(). Also acceptable is a URL to a JSON file.
              
              stackfile:
                *Deprecated* filename for an XML representation of the image stack.
        """
        self.layer = layer
        
        if type(stack) in (str, unicode):
            stack = jsonload(urlopen(urljoin(layer.config.dirpath, stack)).read())
        
        if type(stack) in (list, dict):
            self.stack = build_stack(stack)

        elif stack is None and stackfile:
            #
            # The stackfile argument is super-deprecated.
            #
            stackfile = pathjoin(self.layer.config.dirpath, stackfile)
            stack = parseXML(stackfile).firstChild
            
            assert stack.tagName == 'stack', \
                   'Expecting root element "stack" but got "%s"' % stack.tagName
    
            self.stack = makeStack(stack)
        
        else:
            raise Exception('Note sure what to do with this stack argument: %s' % repr(stack))
        
    def renderTile(self, width, height, srs, coord):
    
        image = PIL.Image.new('RGBA', (width, height), (0, 0, 0, 0))
        
        image = self.stack.render(self.layer.config, image, coord)
        
        return image

class Composite(Provider):
    """ An old name for the Provider class, deprecated for the next version.
    """
    pass

def build_stack(object):
    """ Build up a data structure of Stack and Layer objects from lists of dictionaries.
    
        Normally, this is applied to the "stack" parameter to Composite.Provider.
    """
    if type(object) is list:
        layers = map(build_stack, object)
        return Stack(layers)
    
    elif type(object) is dict:
        keys = ('src', 'layername'), ('color', 'colorname'), \
               ('mask', 'maskname'), ('opacity', 'opacity'), \
               ('mode', 'blendmode'), ('adjustments', 'adjustments')

        args = [(arg, object[key]) for (key, arg) in keys if key in object]
        
        return Layer(**dict(args))

    else:
        raise Exception('Uh oh')

class Layer:
    """ A single image layer in a stack.
    
        Can include a reference to another layer for the source image, a second
        reference to another layer for the mask, and a color name for the fill.
    """
    def __init__(self, layername=None, colorname=None, maskname=None, opacity=1.0, blendmode=None, adjustments=None):
        """ A new image layer.
        
            Arguments:
            
              layername:
                Name of the primary source image layer.
              
              colorname:
                Fill color, passed to make_color().
              
              maskname:
                Name of the mask image layer.
        """
        self.layername = layername
        self.colorname = colorname
        self.maskname = maskname
        self.opacity = opacity
        self.blendmode = blendmode
        self.adjustments = adjustments

    def render(self, config, input_img, coord):
        """ Render this image layer.

            Given a configuration object, starting image, and coordinate,
            return an output image with the contents of this image layer.
        """
        has_layer, has_color, has_mask = False, False, False
        
        output_rgba = _img2rgba(input_img)
    
        if self.layername:
            layer = config.layers[self.layername]
            mime, body = TileStache.getTile(layer, coord, 'png')
            layer_img = PIL.Image.open(StringIO(body)).convert('RGBA')
            layer_rgba = _img2rgba(layer_img)

            has_layer = True
        
        if self.maskname:
            layer = config.layers[self.maskname]
            mime, body = TileStache.getTile(layer, coord, 'png')
            mask_img = PIL.Image.open(StringIO(body)).convert('L')
            mask_chan = _img2arr(mask_img).astype(numpy.float32) / 255.

            has_mask = True

        if self.colorname:
            color = make_color(self.colorname)
            color_rgba = [numpy.zeros(output_rgba[0].shape, numpy.float32) + band/255.0 for band in color]

            has_color = True

        if has_layer:
            layer_rgba = apply_adjustments(layer_rgba, self.adjustments)
        
        if has_layer and has_color and has_mask:
            raise KnownUnknown("You can't specify src, color and mask together in a Composite Layer: %s, %s, %s" % (repr(self.layername), repr(self.colorname), repr(self.maskname)))
        
        elif has_layer and has_color:
            output_rgba = blend_images(output_rgba, color_rgba, color_rgba, self.opacity, self.blendmode)
            output_rgba = blend_images(output_rgba, layer_rgba, layer_rgba, self.opacity, self.blendmode)

        elif has_layer and has_mask:
            # need to combine the masks here
            layermask_rgba = [numpy.zeros(mask_chan.shape, numpy.float32) for i in range(4)]
            layermask_rgba = blend_images(layermask_rgba, layer_rgba, mask_chan, 1, None)

            output_rgba = blend_images(output_rgba, layermask_rgba, layermask_rgba, self.opacity, self.blendmode)

        elif has_color and has_mask:
            output_rgba = blend_images(output_rgba, color_rgba, mask_chan, self.opacity, self.blendmode)
        
        elif has_layer:
            output_rgba = blend_images(output_rgba, layer_rgba, layer_rgba, self.opacity, self.blendmode)
        
        elif has_color:
            output_rgba = blend_images(output_rgba, color_rgba, color_rgba, self.opacity, self.blendmode)

        elif has_mask:
            raise KnownUnknown("You have to provide more than just a mask to Composite Layer: %s" % repr(self.maskname))

        else:
            raise KnownUnknown("You have to at least some combination of src, color and mask to Composite Layer: %s" % repr(self.maskname))

        output_img = _rgba2img(output_rgba)
        
        return output_img

class Stack:
    """ A stack of image layers.
    """
    def __init__(self, layers):
        """ A new image stack.
        
            Argument:
            
              layers:
                List of Layer instances.
        """
        self.layers = layers

    def render(self, config, input_img, coord):
        """ Render this image stack.

            Given a configuration object, starting image, and coordinate,
            return an output image with the results of all the layers in
            this stack pasted on in turn.
        """
        stack_img = PIL.Image.new('RGBA', input_img.size, (0, 0, 0, 0))
        
        for layer in self.layers:
            stack_img = layer.render(config, stack_img, coord)

        output_img = input_img.copy()
        output_img.paste(stack_img, (0, 0), stack_img)
        
        return output_img

def make_color(color):
    """ Convert colors expressed as HTML-style RGB(A) strings to tuples.
        
        Returns four-element RGBA tuple, e.g. (0xFF, 0x99, 0x00, 0xFF).
    
        Examples:
          white: "#ffffff", "#fff", "#ffff", "#ffffffff"
          black: "#000000", "#000", "#000f", "#000000ff"
          null: "#0000", "#00000000"
          orange: "#f90", "#ff9900", "#ff9900ff"
          transparent orange: "#f908", "#ff990088"
    """
    if type(color) not in (str, unicode):
        raise KnownUnknown('Color must be a string: %s' % repr(color))

    if color[0] != '#':
        raise KnownUnknown('Color must start with hash: "%s"' % color)

    if len(color) not in (4, 5, 7, 9):
        raise KnownUnknown('Color must have three, four, six or seven hex chars: "%s"' % color)

    if len(color) == 4:
        color = ''.join([color[i] for i in (0, 1, 1, 2, 2, 3, 3)])

    elif len(color) == 5:
        color = ''.join([color[i] for i in (0, 1, 1, 2, 2, 3, 3, 4, 4)])
    
    try:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        a = len(color) == 7 and 0xFF or int(color[7:9], 16)

    except ValueError:
        raise KnownUnknown('Color must be made up of valid hex chars: "%s"' % color)

    return r, g, b, a

def _arr2img(ar):
    """ Convert Numeric.array to PIL.Image.
    """
    return PIL.Image.fromstring('L', (ar.shape[1], ar.shape[0]), ar.astype(numpy.ubyte).tostring())

def _img2arr(im):
    """ Convert PIL.Image to Numeric.array.
    """
    assert im.mode == 'L'
    return numpy.reshape(numpy.fromstring(im.tostring(), numpy.ubyte), (im.size[1], im.size[0]))

def _rgba2img(rgba):
    """ Convert four Numeric.array objects to PIL.Image.
    """
    assert type(rgba) is list
    return PIL.Image.merge('RGBA', [_arr2img((band * 255.0).astype(numpy.ubyte)) for band in rgba])

def _img2rgba(im):
    """ Convert PIL.Image to four Numeric.array objects.
    """
    assert im.mode == 'RGBA'
    return [_img2arr(band).astype(numpy.float32) / 255.0 for band in im.split()]

def apply_adjustments(rgba, adjustments):
    """ Apply image adjustments one by one and return a modified image.
    
        Working adjustments:
        
          curves:
            Calls apply_curves_adjustment()
    """
    if not adjustments:
        return rgba

    for (name, args) in adjustments:
        if name == 'curves':
            rgba = apply_curves_adjustment(rgba, *args)
        
        else:
            raise KnownUnknown('Unrecognized composite adjustment: "%s" with args %s' % (name, repr(args)))
    
    return rgba

def apply_curves_adjustment(rgba, black, grey, white):
    """ *write me*
    """
    # channels
    red, green, blue, alpha = rgba
    
    # coefficients
    a, b, c = [sympy.Symbol(n) for n in 'abc']
    
    # knowns
    black, grey, white = black / 255.0, grey / 255.0, white / 255.0
    
    # black, gray, white
    eqs = [a * black**2 + b * black + c - 0.0,
           a *  grey**2 + b *  grey + c - 0.5,
           a * white**2 + b * white + c - 1.0]
    
    co = sympy.solve(eqs, a, b, c)
    
    # arrays for each coefficient
    a, b, c = [float(co[n]) * numpy.ones(red.shape, numpy.float32) for n in (a, b, c)]
    
    # arithmetic
    red   = a * red**2   + b * red   + c
    green = a * green**2 + b * green + c
    blue  = a * blue**2  + b * blue  + c
    
    return red, green, blue, alpha

def blend_images(bottom_rgba, top_rgba, mask_chan, opacity, blendmode):
    """ Blend images using a given mask, opacity, and blend mode.
    
        Working blend modes:
          
          None:
            Plain old pass-through blend.
        
          "hard light":
            Apply http://illusions.hu/effectwiki/doku.php?id=hard_light_blending
    """
    output_rgba = [numpy.copy(chan) for chan in bottom_rgba]

    if opacity == 0:
        # no-op
        return output_rgba
    
    if type(mask_chan) is list:
        # use just the alpha channel if it's given as full RGBA.
        mask_chan = mask_chan[3]
    
    if not blendmode:
        # plain old paste
        for c in (0, 1, 2):
            output_rgba[c] = (1 - mask_chan) * bottom_rgba[c] + mask_chan * top_rgba[c]

        # combine all three alphas
        intersect_chan = top_rgba[3] * mask_chan
        output_rgba[3] = 1 - (1 - bottom_rgba[3]) * (1 - intersect_chan)

    elif blendmode == 'hard light':
        # http://illusions.hu/effectwiki/doku.php?id=hard_light_blending
        for c in (0, 1, 2):
            dk, lt = top_rgba[c] < .5, top_rgba[c] >= .5
            
            output_rgba[c][dk] = 2 * bottom_rgba[c][dk] * top_rgba[c][dk]
            output_rgba[c][lt] = 1 - 2 * (1 - bottom_rgba[c][lt]) * (1 - top_rgba[c][lt])
    
    else:
        raise KnownUnknown('Unrecognized blend mode: "%s"' % blendmode)

    for c in (0, 1, 2):
        output_rgba[c] = (1 - opacity) * bottom_rgba[c] + opacity * output_rgba[c]
    
    return output_rgba

def makeColor(color):
    """ An old name for the make_color function, deprecated for the next version.
    """
    return make_color(color)
    
def makeLayer(element):
    """ Build a Layer object from an XML element, deprecated for the next version.
    """
    kwargs = {}
    
    if element.hasAttribute('src'):
        kwargs['layername'] = element.getAttribute('src')

    if element.hasAttribute('color'):
        kwargs['colorname'] = element.getAttribute('color')
    
    for child in element.childNodes:
        if child.nodeType == child.ELEMENT_NODE:
            if child.tagName == 'mask' and child.hasAttribute('src'):
                kwargs['maskname'] = child.getAttribute('src')

    print >> sys.stderr, 'Making a layer from', kwargs
    
    return Layer(**kwargs)

def makeStack(element):
    """ Build a Stack object from an XML element, deprecated for the next version.
    """
    layers = []
    
    for child in element.childNodes:
        if child.nodeType == child.ELEMENT_NODE:
            if child.tagName == 'stack':
                stack = makeStack(child)
                layers.append(stack)
            
            elif child.tagName == 'layer':
                layer = makeLayer(child)
                layers.append(layer)

            else:
                raise Exception('Unknown element "%s"' % child.tagName)

    print >> sys.stderr, 'Making a stack with %d layers' % len(layers)

    return Stack(layers)

if __name__ == '__main__':

    import unittest
    
    import TileStache.Core
    import TileStache.Caches
    import TileStache.Geography
    import TileStache.Config
    import ModestMaps.Core
    
    class TinyBitmap:
        """ A minimal provider that only returns 3x3 bitmaps from strings.
        """
        def __init__(self, string):
            self.img = PIL.Image.fromstring('RGBA', (3, 3), string)

        def renderTile(self, *args, **kwargs):
            return self.img

    def tinybitmap_layer(config, string):
        """ Gin up a fake layer with a TinyBitmap provider.
        """
        meta = TileStache.Core.Metatile()
        proj = TileStache.Geography.SphericalMercator()
        layer = TileStache.Core.Layer(config, proj, meta)
        layer.provider = TinyBitmap(string)

        return layer

    def minimal_stack_layer(config, stack):
        """
        """
        meta = TileStache.Core.Metatile()
        proj = TileStache.Geography.SphericalMercator()
        layer = TileStache.Core.Layer(config, proj, meta)
        layer.provider = Provider(layer, stack=stack)

        return layer
    
    class ColorTests(unittest.TestCase):
        """
        """
        def testColors(self):
            assert make_color('#ffffff') == (0xFF, 0xFF, 0xFF, 0xFF), 'white'
            assert make_color('#fff') == (0xFF, 0xFF, 0xFF, 0xFF), 'white again'
            assert make_color('#ffff') == (0xFF, 0xFF, 0xFF, 0xFF), 'white again again'
            assert make_color('#ffffffff') == (0xFF, 0xFF, 0xFF, 0xFF), 'white again again again'

            assert make_color('#000000') == (0x00, 0x00, 0x00, 0xFF), 'black'
            assert make_color('#000') == (0x00, 0x00, 0x00, 0xFF), 'black again'
            assert make_color('#000f') == (0x00, 0x00, 0x00, 0xFF), 'black again'
            assert make_color('#000000ff') == (0x00, 0x00, 0x00, 0xFF), 'black again again'

            assert make_color('#0000') == (0x00, 0x00, 0x00, 0x00), 'null'
            assert make_color('#00000000') == (0x00, 0x00, 0x00, 0x00), 'null again'

            assert make_color('#f90') == (0xFF, 0x99, 0x00, 0xFF), 'orange'
            assert make_color('#ff9900') == (0xFF, 0x99, 0x00, 0xFF), 'orange again'
            assert make_color('#ff9900ff') == (0xFF, 0x99, 0x00, 0xFF), 'orange again again'

            assert make_color('#f908') == (0xFF, 0x99, 0x00, 0x88), 'transparent orange'
            assert make_color('#ff990088') == (0xFF, 0x99, 0x00, 0x88), 'transparent orange again'
        
        def testErrors(self):

            # it has to be a string
            self.assertRaises(KnownUnknown, make_color, True)
            self.assertRaises(KnownUnknown, make_color, None)
            self.assertRaises(KnownUnknown, make_color, 1337)
            self.assertRaises(KnownUnknown, make_color, [93])
            
            # it has to start with a hash
            self.assertRaises(KnownUnknown, make_color, 'hello')
            
            # it has to have 3, 4, 6 or 7 hex chars
            self.assertRaises(KnownUnknown, make_color, '#00')
            self.assertRaises(KnownUnknown, make_color, '#00000')
            self.assertRaises(KnownUnknown, make_color, '#0000000')
            self.assertRaises(KnownUnknown, make_color, '#000000000')
            
            # they have to actually hex chars
            self.assertRaises(KnownUnknown, make_color, '#foo')
            self.assertRaises(KnownUnknown, make_color, '#bear')
            self.assertRaises(KnownUnknown, make_color, '#monkey')
            self.assertRaises(KnownUnknown, make_color, '#dedboeuf')
    
    class CompositeTests(unittest.TestCase):
        """
        """
        def setUp(self):
    
            cache = TileStache.Caches.Test()
            self.config = TileStache.Config.Configuration(cache, '.')
            
            # Sort of a sw/ne diagonal street, with a top-left corner halo:
            # 
            # +------+   +------+   +------+   +------+   +------+
            # |\\\\\\|   |++++--|   |  ////|   |    ''|   |\\//''|
            # |\\\\\\| + |++++--| + |//////| + |  ''  | > |//''\\|
            # |\\\\\\|   |------|   |////  |   |''    |   |''\\\\|
            # +------+   +------+   +------+   +------+   +------+
            # base       halos      outlines   streets    output
            #
            # Just trust the tests.
            #
            _fff, _ccc, _999, _000, _nil = '\xFF\xFF\xFF\xFF', '\xCC\xCC\xCC\xFF', '\x99\x99\x99\xFF', '\x00\x00\x00\xFF', '\x00\x00\x00\x00'
            
            self.config.layers = \
            {
                'base':     tinybitmap_layer(self.config, _ccc * 9),
                'halos':    tinybitmap_layer(self.config, _fff + _fff + _000 + _fff + _fff + (_000 * 4)),
                'outlines': tinybitmap_layer(self.config, _nil + (_999 * 7) + _nil),
                'streets':  tinybitmap_layer(self.config, _nil + _nil + _fff + _nil + _fff + _nil + _fff + _nil + _nil)
            }
            
            self.start_img = PIL.Image.new('RGBA', (3, 3), (0x00, 0x00, 0x00, 0x00))
        
        def test0(self):
    
            stack = \
                [
                    {"src": "base"},
                    [
                        {"src": "outlines"},
                        {"src": "streets"}
                    ]
                ]
            
            layer = minimal_stack_layer(self.config, stack)
            img = layer.provider.renderTile(3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))
            
            assert img.getpixel((0, 0)) == (0xCC, 0xCC, 0xCC, 0xFF), 'top left pixel'
            assert img.getpixel((1, 0)) == (0x99, 0x99, 0x99, 0xFF), 'top center pixel'
            assert img.getpixel((2, 0)) == (0xFF, 0xFF, 0xFF, 0xFF), 'top right pixel'
            assert img.getpixel((0, 1)) == (0x99, 0x99, 0x99, 0xFF), 'center left pixel'
            assert img.getpixel((1, 1)) == (0xFF, 0xFF, 0xFF, 0xFF), 'middle pixel'
            assert img.getpixel((2, 1)) == (0x99, 0x99, 0x99, 0xFF), 'center right pixel'
            assert img.getpixel((0, 2)) == (0xFF, 0xFF, 0xFF, 0xFF), 'bottom left pixel'
            assert img.getpixel((1, 2)) == (0x99, 0x99, 0x99, 0xFF), 'bottom center pixel'
            assert img.getpixel((2, 2)) == (0xCC, 0xCC, 0xCC, 0xFF), 'bottom right pixel'
        
        def test1(self):
    
            stack = \
                [
                    {"src": "base"},
                    [
                        {"src": "outlines", "mask": "halos"},
                        {"src": "streets"}
                    ]
                ]
            
            layer = minimal_stack_layer(self.config, stack)
            img = layer.provider.renderTile(3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))
            
            assert img.getpixel((0, 0)) == (0xCC, 0xCC, 0xCC, 0xFF), 'top left pixel'
            assert img.getpixel((1, 0)) == (0x99, 0x99, 0x99, 0xFF), 'top center pixel'
            assert img.getpixel((2, 0)) == (0xFF, 0xFF, 0xFF, 0xFF), 'top right pixel'
            assert img.getpixel((0, 1)) == (0x99, 0x99, 0x99, 0xFF), 'center left pixel'
            assert img.getpixel((1, 1)) == (0xFF, 0xFF, 0xFF, 0xFF), 'middle pixel'
            assert img.getpixel((2, 1)) == (0xCC, 0xCC, 0xCC, 0xFF), 'center right pixel'
            assert img.getpixel((0, 2)) == (0xFF, 0xFF, 0xFF, 0xFF), 'bottom left pixel'
            assert img.getpixel((1, 2)) == (0xCC, 0xCC, 0xCC, 0xFF), 'bottom center pixel'
            assert img.getpixel((2, 2)) == (0xCC, 0xCC, 0xCC, 0xFF), 'bottom right pixel'
        
        def test2(self):
    
            stack = \
                [
                    {"color": "#ccc"},
                    [
                        {"src": "outlines", "mask": "halos"},
                        {"src": "streets"}
                    ]
                ]
            
            layer = minimal_stack_layer(self.config, stack)
            img = layer.provider.renderTile(3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))
            
            assert img.getpixel((0, 0)) == (0xCC, 0xCC, 0xCC, 0xFF), 'top left pixel'
            assert img.getpixel((1, 0)) == (0x99, 0x99, 0x99, 0xFF), 'top center pixel'
            assert img.getpixel((2, 0)) == (0xFF, 0xFF, 0xFF, 0xFF), 'top right pixel'
            assert img.getpixel((0, 1)) == (0x99, 0x99, 0x99, 0xFF), 'center left pixel'
            assert img.getpixel((1, 1)) == (0xFF, 0xFF, 0xFF, 0xFF), 'middle pixel'
            assert img.getpixel((2, 1)) == (0xCC, 0xCC, 0xCC, 0xFF), 'center right pixel'
            assert img.getpixel((0, 2)) == (0xFF, 0xFF, 0xFF, 0xFF), 'bottom left pixel'
            assert img.getpixel((1, 2)) == (0xCC, 0xCC, 0xCC, 0xFF), 'bottom center pixel'
            assert img.getpixel((2, 2)) == (0xCC, 0xCC, 0xCC, 0xFF), 'bottom right pixel'
        
        def test3(self):
            
            stack = \
                [
                    {"color": "#ccc"},
                    [
                        {"color": "#999", "mask": "halos"},
                        {"src": "streets"}
                    ]
                ]
            
            layer = minimal_stack_layer(self.config, stack)
            img = layer.provider.renderTile(3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))
            
            assert img.getpixel((0, 0)) == (0x99, 0x99, 0x99, 0xFF), 'top left pixel'
            assert img.getpixel((1, 0)) == (0x99, 0x99, 0x99, 0xFF), 'top center pixel'
            assert img.getpixel((2, 0)) == (0xFF, 0xFF, 0xFF, 0xFF), 'top right pixel'
            assert img.getpixel((0, 1)) == (0x99, 0x99, 0x99, 0xFF), 'center left pixel'
            assert img.getpixel((1, 1)) == (0xFF, 0xFF, 0xFF, 0xFF), 'middle pixel'
            assert img.getpixel((2, 1)) == (0xCC, 0xCC, 0xCC, 0xFF), 'center right pixel'
            assert img.getpixel((0, 2)) == (0xFF, 0xFF, 0xFF, 0xFF), 'bottom left pixel'
            assert img.getpixel((1, 2)) == (0xCC, 0xCC, 0xCC, 0xFF), 'bottom center pixel'
            assert img.getpixel((2, 2)) == (0xCC, 0xCC, 0xCC, 0xFF), 'bottom right pixel'
        
        def test4(self):
    
            stack = \
                [
                    [
                        {"color": "#999", "mask": "halos"},
                        {"src": "streets"}
                    ]
                ]
            
            layer = minimal_stack_layer(self.config, stack)
            img = layer.provider.renderTile(3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))
            
            assert img.getpixel((0, 0)) == (0x99, 0x99, 0x99, 0xFF), 'top left pixel'
            assert img.getpixel((1, 0)) == (0x99, 0x99, 0x99, 0xFF), 'top center pixel'
            assert img.getpixel((2, 0)) == (0xFF, 0xFF, 0xFF, 0xFF), 'top right pixel'
            assert img.getpixel((0, 1)) == (0x99, 0x99, 0x99, 0xFF), 'center left pixel'
            assert img.getpixel((1, 1)) == (0xFF, 0xFF, 0xFF, 0xFF), 'middle pixel'
            assert img.getpixel((2, 1)) == (0x00, 0x00, 0x00, 0x00), 'center right pixel'
            assert img.getpixel((0, 2)) == (0xFF, 0xFF, 0xFF, 0xFF), 'bottom left pixel'
            assert img.getpixel((1, 2)) == (0x00, 0x00, 0x00, 0x00), 'bottom center pixel'
            assert img.getpixel((2, 2)) == (0x00, 0x00, 0x00, 0x00), 'bottom right pixel'
        
        def test5(self):

            stack = {"src": "streets", "color": "#999", "mask": "halos"}
            layer = minimal_stack_layer(self.config, stack)
            
            # it's an error to specify scr, color, and mask all together
            self.assertRaises(KnownUnknown, layer.provider.renderTile, 3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))

            stack = {"mask": "halos"}
            layer = minimal_stack_layer(self.config, stack)
            
            # it's also an error to specify just a mask
            self.assertRaises(KnownUnknown, layer.provider.renderTile, 3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))

            stack = {}
            layer = minimal_stack_layer(self.config, stack)
            
            # an empty stack is not so great
            self.assertRaises(KnownUnknown, layer.provider.renderTile, 3, 3, None, ModestMaps.Core.Coordinate(0, 0, 0))

    unittest.main()
