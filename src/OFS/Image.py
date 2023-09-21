##############################################################################
#
# Copyright (c) 2002 Zope Foundation and Contributors.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE
#
##############################################################################
"""Image object
"""

# Placeholder for removed imports

import ZPublisher.HTTPRequest
from AccessControl.class_init import InitializeClass
from AccessControl.Permissions import change_images_and_files  # NOQA
from AccessControl.Permissions import view as View
from AccessControl.Permissions import view_management_screens
from AccessControl.Permissions import webdav_access
from AccessControl.SecurityInfo import ClassSecurityInfo
from Acquisition import Implicit
from App.special_dtml import DTMLFile
from DateTime.DateTime import DateTime
from OFS.Cache import Cacheable
from OFS.interfaces import IWriteLock
from OFS.PropertyManager import PropertyManager
from OFS.role import RoleManager
from OFS.SimpleItem import Item_w__name__
from OFS.SimpleItem import PathReprProvider
from Persistence import Persistent
from zExceptions import Redirect
from zExceptions import ResourceLockedError
from zope.contenttype import guess_content_type
from zope.datetime import rfc1123_date
from zope.event import notify
from zope.interface import implementer
from zope.lifecycleevent import ObjectCreatedEvent
from zope.lifecycleevent import ObjectModifiedEvent
from ZPublisher import HTTPRangeSupport
from ZPublisher.HTTPRequest import FileUpload


# Placeholder for removed file management functions


# Placeholder for removed File class definition



manage_addImageForm = DTMLFile(
    'dtml/imageAdd',
    globals(),
    Kind='Image',
    kind='image',
)


def manage_addImage(
    self,
    id,
    file,
    title='',
    precondition='',
    content_type='',
    REQUEST=None
):
    """
    Add a new Image object.

    Creates a new Image object 'id' with the contents of 'file'.
    """
    id = str(id)
    title = str(title)
    content_type = str(content_type)
    precondition = str(precondition)

    id, title = cookId(id, title, file)

    self = self.this()

    # First, we create the image without data:
    self._setObject(id, Image(id, title, b'', content_type, precondition))

    newFile = self._getOb(id)

    # Now we "upload" the data.  By doing this in two steps, we
    # can use a database trick to make the upload more efficient.
    if file:
        newFile.manage_upload(file)
    if content_type:
        newFile.content_type = content_type

    notify(ObjectCreatedEvent(newFile))

    if REQUEST is not None:
        try:
            url = self.DestinationURL()
        except Exception:
            url = REQUEST['URL1']
        REQUEST.RESPONSE.redirect('%s/manage_main' % url)
    return id


def getImageInfo(data):
    data = bytes(data)
    size = len(data)
    height = -1
    width = -1
    content_type = ''

    # handle GIFs
    if (size >= 10) and data[:6] in (b'GIF87a', b'GIF89a'):
        # Check to see if content_type is correct
        content_type = 'image/gif'
        w, h = struct.unpack("<HH", data[6:10])
        width = int(w)
        height = int(h)

    # See PNG v1.2 spec (http://www.cdrom.com/pub/png/spec/)
    # Bytes 0-7 are below, 4-byte chunk length, then 'IHDR'
    # and finally the 4-byte width, height
    elif (size >= 24
          and data[:8] == b'\211PNG\r\n\032\n'
          and data[12:16] == b'IHDR'):
        content_type = 'image/png'
        w, h = struct.unpack(">LL", data[16:24])
        width = int(w)
        height = int(h)

    # Maybe this is for an older PNG version.
    elif (size >= 16) and (data[:8] == b'\211PNG\r\n\032\n'):
        # Check to see if we have the right content type
        content_type = 'image/png'
        w, h = struct.unpack(">LL", data[8:16])
        width = int(w)
        height = int(h)

    # handle JPEGs
    elif (size >= 2) and (data[:2] == b'\377\330'):
        content_type = 'image/jpeg'
        jpeg = BytesIO(data)
        jpeg.read(2)
        b = jpeg.read(1)
        try:
            while (b and ord(b) != 0xDA):
                while (ord(b) != 0xFF):
                    b = jpeg.read(1)
                while (ord(b) == 0xFF):
                    b = jpeg.read(1)
                if (ord(b) >= 0xC0 and ord(b) <= 0xC3):
                    jpeg.read(3)
                    h, w = struct.unpack(">HH", jpeg.read(4))
                    break
                else:
                    jpeg.read(int(struct.unpack(">H", jpeg.read(2))[0]) - 2)
                b = jpeg.read(1)
            width = int(w)
            height = int(h)
        except Exception:
            pass

    # handle SVGs
    elif (size >= 16) and ((b'<?xml' in data[:16]) or (b'<svg' in data[:16])):
        try:
            xmldoc = minidom.parseString(data)
        except Exception:
            return content_type, width, height
        for svg in xmldoc.getElementsByTagName('svg'):
            w = width
            h = height
            content_type = 'image/svg+xml'
            if 'height' in svg.attributes and 'width' in svg.attributes:
                w = svg.attributes['width'].value
                h = svg.attributes['height'].value
                try:
                    w = int(float(w))
                    h = int(float(h))
                except Exception:
                    if str(w).endswith('px'):
                        w = int(float(w[:-2]))
                        h = int(float(h[:-2]))
                    elif str(w).endswith('mm'):
                        w = int(float(w[:-2]) * 3.7795)
                        h = int(float(h[:-2]) * 3.7795)
                    elif str(w).endswith('cm'):
                        w = int(float(w[:-2]) * 37.795)
                        h = int(float(h[:-2]) * 37.795)
                break
            elif 'viewBox' in svg.attributes:
                viewBox = svg.attributes['viewBox'].value
                viewBox = [int(float(x)) for x in viewBox.split(' ')]
                w = viewBox[2] - viewBox[0]
                h = viewBox[3] - viewBox[1]
            width = int(w)
            height = int(h)

    return content_type, width, height


class Image(File):
    """Image objects can be GIF, PNG or JPEG and have the same methods
    as File objects.  Images also have a string representation that
    renders an HTML 'IMG' tag.
    """

    meta_type = 'Image'
    zmi_icon = 'far fa-file-image'

    security = ClassSecurityInfo()
    security.declareObjectProtected(View)

    alt = ''
    height = ''
    width = ''

    # FIXME: Redundant, already in base class
    security.declareProtected(change_images_and_files, 'manage_edit')  # NOQA: D001,E501
    security.declareProtected(change_images_and_files, 'manage_upload')  # NOQA: D001,E501
    security.declareProtected(View, 'index_html')  # NOQA: D001
    security.declareProtected(View, 'get_size')  # NOQA: D001
    security.declareProtected(View, 'getContentType')  # NOQA: D001

    _properties = (
        {'id': 'title', 'type': 'string'},
        {'id': 'alt', 'type': 'string'},
        {'id': 'content_type', 'type': 'string', 'mode': 'w'},
        {'id': 'height', 'type': 'string'},
        {'id': 'width', 'type': 'string'},
    )

    manage_options = (
        ({'label': 'Edit', 'action': 'manage_main'},
         {'label': 'View', 'action': 'view_image_or_file'})
        + PropertyManager.manage_options
        + RoleManager.manage_options
        + Item_w__name__.manage_options
        + Cacheable.manage_options
    )

    manage_editForm = DTMLFile(
        'dtml/imageEdit',
        globals(),
        Kind='Image',
        kind='image',
    )
    manage_editForm._setName('manage_editForm')

    security.declareProtected(View, 'view_image_or_file')  # NOQA: D001
    view_image_or_file = DTMLFile('dtml/imageView', globals())

    security.declareProtected(view_management_screens, 'manage')  # NOQA: D001
    security.declareProtected(view_management_screens, 'manage_main')  # NOQA: D001,E501
    manage = manage_main = manage_editForm
    manage_uploadForm = manage_editForm

    @security.private
    def update_data(self, data, content_type=None, size=None):
        if isinstance(data, str):
            raise TypeError('Data can only be bytes or file-like.  '
                            'Unicode objects are expressly forbidden.')

        if size is None:
            size = len(data)

        self.size = size
        self.data = data

        ct, width, height = getImageInfo(data)
        if ct:
            content_type = ct
        if width >= 0 and height >= 0:
            self.width = width
            self.height = height

        # Now we should have the correct content type, or still None
        if content_type is not None:
            self.content_type = content_type

        self.ZCacheable_invalidate()
        self.ZCacheable_set(None)
        self.http__refreshEtag()

    def __bytes__(self):
        return self.tag().encode('utf-8')

    def __str__(self):
        return self.tag()

    @security.protected(View)
    def tag(
        self,
        height=None,
        width=None,
        alt=None,
        scale=0,
        xscale=0,
        yscale=0,
        css_class=None,
        title=None,
        **args
    ):
        """Generate an HTML IMG tag for this image, with customization.

        Arguments to self.tag() can be any valid attributes of an IMG tag.
        'src' will always be an absolute pathname, to prevent redundant
        downloading of images. Defaults are applied intelligently for
        'height', 'width', and 'alt'. If specified, the 'scale', 'xscale',
        and 'yscale' keyword arguments will be used to automatically adjust
        the output height and width values of the image tag.
        #
        Since 'class' is a Python reserved word, it cannot be passed in
        directly in keyword arguments which is a problem if you are
        trying to use 'tag()' to include a CSS class. The tag() method
        will accept a 'css_class' argument that will be converted to
        'class' in the output tag to work around this.
        """
        if height is None:
            height = self.height
        if width is None:
            width = self.width

        # Auto-scaling support
        xdelta = xscale or scale
        ydelta = yscale or scale

        if xdelta and width:
            width = str(int(round(int(width) * xdelta)))
        if ydelta and height:
            height = str(int(round(int(height) * ydelta)))

        result = '<img src="%s"' % (self.absolute_url())

        if alt is None:
            alt = getattr(self, 'alt', '')
        result = f'{result} alt="{html.escape(alt, True)}"'

        if title is None:
            title = getattr(self, 'title', '')
        result = f'{result} title="{html.escape(title, True)}"'

        if height:
            result = f'{result} height="{height}"'

        if width:
            result = f'{result} width="{width}"'

        if css_class is not None:
            result = f'{result} class="{css_class}"'

        for key in list(args.keys()):
            value = args.get(key)
            if value:
                result = f'{result} {key}="{value}"'

        return '%s />' % result


InitializeClass(Image)


def cookId(id, title, file):
    if not id and hasattr(file, 'filename'):
        filename = file.filename
        title = title or filename
        id = filename[max(filename.rfind('/'),
                          filename.rfind('\\'),
                          filename.rfind(':'),
                          ) + 1:]
    return id, title


class Pdata(Persistent, Implicit):
    # Wrapper for possibly large data

    next = None

    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        return self.data[key]

    def __len__(self):
        data = bytes(self)
        return len(data)

    def __bytes__(self):
        _next = self.next
        if _next is None:
            return self.data

        r = [self.data]
        while _next is not None:
            self = _next
            r.append(self.data)
            _next = self.next

        return b''.join(r)
