"""Entry model for Zinnia"""
import warnings

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.html import linebreaks
from django.contrib.sites.models import Site
from django.utils.importlib import import_module
from django.utils.functional import cached_property
from django.contrib import comments
from django.contrib.comments.models import CommentFlag
from django.utils.translation import ugettext_lazy as _

from django.contrib.markup.templatetags.markup import markdown
from django.contrib.markup.templatetags.markup import textile
from django.contrib.markup.templatetags.markup import restructuredtext

from tagging.fields import TagField
from tagging.utils import parse_tag_input

from zinnia.models.author import Author
from zinnia.models.category import Category
from zinnia.flags import PINGBACK, TRACKBACK
from zinnia.settings import UPLOAD_TO
from zinnia.settings import MARKUP_LANGUAGE
from zinnia.settings import ENTRY_TEMPLATES
from zinnia.settings import ENTRY_BASE_MODEL
from zinnia.settings import MARKDOWN_EXTENSIONS
from zinnia.settings import AUTO_CLOSE_COMMENTS_AFTER
from zinnia.managers import entries_published
from zinnia.managers import EntryPublishedManager
from zinnia.managers import DRAFT, HIDDEN, PUBLISHED
from zinnia.url_shortener import get_url_shortener


class CoreEntry(models.Model):
    """
    Abstract core entry model class providing
    the fields and methods required for publishing
    content over time.
    """
    STATUS_CHOICES = ((DRAFT, _('draft')),
                      (HIDDEN, _('hidden')),
                      (PUBLISHED, _('published')))

    title = models.CharField(
        _('title'), max_length=255)

    content = models.TextField(_('content'))

    slug = models.SlugField(
        _('slug'), max_length=255,
        unique_for_date='creation_date',
        help_text=_("Used to build the entry's URL."))

    status = models.IntegerField(
        _('status'), choices=STATUS_CHOICES, default=DRAFT)

    start_publication = models.DateTimeField(
        _('start publication'), blank=True, null=True,
        help_text=_('Start date of publication.'))

    end_publication = models.DateTimeField(
        _('end publication'), blank=True, null=True,
        help_text=_('End date of publication.'))

    sites = models.ManyToManyField(
        Site,
        related_name='entries',
        verbose_name=_('sites'),
        help_text=_('Sites where the entry will be published.'))

    creation_date = models.DateTimeField(
        _('creation date'), default=timezone.now,
        help_text=_("Used to build the entry's URL."))

    last_update = models.DateTimeField(
        _('last update'), default=timezone.now)

    objects = models.Manager()
    published = EntryPublishedManager()

    @property
    def html_content(self):
        """
        Returns the "content" field formatted in HTML.
        """
        if MARKUP_LANGUAGE == 'markdown':
            return markdown(self.content, MARKDOWN_EXTENSIONS)
        elif MARKUP_LANGUAGE == 'textile':
            return textile(self.content)
        elif MARKUP_LANGUAGE == 'restructuredtext':
            return restructuredtext(self.content)
        elif not '</p>' in self.content:
            return linebreaks(self.content)
        return self.content

    @property
    def word_count(self):
        """
        Counts the number of words used in an entry.
        """
        return len(strip_tags(self.html_content).split())

    @property
    def is_actual(self):
        """
        Checks if an entry is within his publication period.
        """
        now = timezone.now()
        if self.start_publication and now < self.start_publication:
            return False

        if self.end_publication and now >= self.end_publication:
            return False
        return True

    @property
    def is_visible(self):
        """
        Checks if an entry is visible and published.
        """
        return self.is_actual and self.status == PUBLISHED

    @cached_property
    def previous_entry(self):
        """
        Returns the previous published entry if exists.
        """
        entries = Entry.published.filter(
            creation_date__lt=self.creation_date)[:1]
        if entries:
            return entries[0]

    @cached_property
    def next_entry(self):
        """
        Returns the next published entry if exists.
        """
        entries = Entry.published.filter(
            creation_date__gt=self.creation_date).order_by('creation_date')[:1]
        if entries:
            return entries[0]

    @property
    def short_url(self):
        """
        Returns the entry's short url.
        """
        return get_url_shortener()(self)

    @models.permalink
    def get_absolute_url(self):
        """
        Builds and returns the entry's URL based on
        the slug and the creation date.
        """
        creation_date = timezone.localtime(self.creation_date)
        return ('zinnia_entry_detail', (), {
            'year': creation_date.strftime('%Y'),
            'month': creation_date.strftime('%m'),
            'day': creation_date.strftime('%d'),
            'slug': self.slug})

    def __unicode__(self):
        return u'%s: %s' % (self.title, self.get_status_display())

    class Meta:
        """
        CoreEntry's meta informations.
        """
        abstract = True
        app_label = 'zinnia'
        ordering = ['-creation_date']
        get_latest_by = 'creation_date'
        verbose_name = _('entry')
        verbose_name_plural = _('entries')
        permissions = (('can_view_all', 'Can view all entries'),
                       ('can_change_status', 'Can change status'),
                       ('can_change_author', 'Can change author(s)'), )


class DiscussionsEntry(models.Model):
    """
    Abstract discussion model class providing
    the fields and methods to manage the discussions
    (comments, pingbacks, trackbacks).
    """
    comment_enabled = models.BooleanField(
        _('comments enabled'), default=True,
        help_text=_('Allows comments if checked.'))
    pingback_enabled = models.BooleanField(
        _('pingbacks enabled'), default=True,
        help_text=_('Allows pingbacks if checked.'))
    trackback_enabled = models.BooleanField(
        _('trackbacks enabled'), default=True,
        help_text=_('Allows trackbacks if checked.'))

    comment_count = models.IntegerField(
        _('comment count'), default=0)
    pingback_count = models.IntegerField(
        _('pingback count'), default=0)
    trackback_count = models.IntegerField(
        _('trackback count'), default=0)

    @property
    def discussions(self):
        """
        Returns a queryset of the published discussions.
        """
        return comments.get_model().objects.for_model(
            self).filter(is_public=True, is_removed=False)

    @property
    def comments(self):
        """
        Returns a queryset of the published comments.
        """
        return self.discussions.filter(Q(flags=None) | Q(
            flags__flag=CommentFlag.MODERATOR_APPROVAL))

    @property
    def pingbacks(self):
        """
        Returns a queryset of the published pingbacks.
        """
        return self.discussions.filter(flags__flag=PINGBACK)

    @property
    def trackbacks(self):
        """
        Return a queryset of the published trackbacks.
        """
        return self.discussions.filter(flags__flag=TRACKBACK)

    @property
    def comments_are_open(self):
        """
        Checks if the comments are open with the
        AUTO_CLOSE_COMMENTS_AFTER setting.
        """
        if AUTO_CLOSE_COMMENTS_AFTER and self.comment_enabled:
            return (timezone.now() - (
                self.start_publication or self.creation_date)).days < \
                AUTO_CLOSE_COMMENTS_AFTER
        return self.comment_enabled

    class Meta:
        abstract = True


class RelatedEntry(models.Model):
    """
    Abstract model class for making manual relations
    between the differents entries.
    """
    related = models.ManyToManyField(
        'self',
        blank=True, null=True,
        verbose_name=_('related entries'))

    @property
    def related_published(self):
        """
        Returns only related entries published.
        """
        return entries_published(self.related)

    class Meta:
        abstract = True


class ExcerptEntry(models.Model):
    """
    Abstract model class to add an excerpt to the entries.
    """
    excerpt = models.TextField(
        _('excerpt'), blank=True,
        help_text=_('Optional element.'))

    class Meta:
        abstract = True


class ImageEntry(models.Model):
    """
    Abstract model class to add an image to the entries.
    """
    image = models.ImageField(
        _('image'), blank=True, upload_to=UPLOAD_TO,
        help_text=_('Used for illustration.'))

    class Meta:
        abstract = True


class FeaturedEntry(models.Model):
    """
    Abstract model class to mark entries as featured.
    """
    featured = models.BooleanField(
        _('featured'), default=False)

    class Meta:
        abstract = True


class AuthorsEntry(models.Model):
    """
    Abstract model class to add relationship
    between the entries and their authors.
    """
    authors = models.ManyToManyField(
        Author,
        related_name='entries',
        blank=True, null=False,
        verbose_name=_('authors'))

    class Meta:
        abstract = True


class CategoriesEntry(models.Model):
    """
    Abstract model class to categorize the entries.
    """
    categories = models.ManyToManyField(
        Category,
        related_name='entries',
        blank=True, null=True,
        verbose_name=_('categories'))

    class Meta:
        abstract = True


class TagsEntry(models.Model):
    """
    Abstract lodel class to add tags to the entries.
    """
    tags = TagField(_('tags'))

    @property
    def tags_list(self):
        """
        Return iterable list of tags.
        """
        return parse_tag_input(self.tags)

    class Meta:
        abstract = True


class LoginRequiredEntry(models.Model):
    """
    Abstract model class to restrcit the display
    of the entry on authenticated users.
    """
    login_required = models.BooleanField(
        _('login required'), default=False,
        help_text=_('Only authenticated users can view the entry.'))

    class Meta:
        abstract = True


class PasswordRequiredEntry(models.Model):
    """
    Abstract model class to restrict the display
    of the entry to users knowing the password.
    """
    password = models.CharField(
        _('password'), max_length=50, blank=True,
        help_text=_('Protects the entry with a password.'))

    class Meta:
        abstract = True


class DetailTemplateEntry(models.Model):
    """
    Abstract model class to display entries with a
    custom template if needed on the detail page.
    """
    template = models.CharField(
        _('template'), max_length=250,
        default='entry_detail.html',
        choices=[('entry_detail.html', _('Default template'))] +
        ENTRY_TEMPLATES,
        help_text=_("Template used to display the entry's detail page."))

    class Meta:
        abstract = True


class EntryAbstractClass(
        CoreEntry,
        DiscussionsEntry,
        RelatedEntry,
        ExcerptEntry,
        ImageEntry,
        FeaturedEntry,
        AuthorsEntry,
        CategoriesEntry,
        TagsEntry,
        LoginRequiredEntry,
        PasswordRequiredEntry,
        DetailTemplateEntry):
    """
    Final abstract entry model class assembling
    all the abstract entry model classes into a single one.

    In this manner we can override some fields without
    reimplemting all the EntryAbstractClass.
    """

    class Meta(CoreEntry.Meta):
        abstract = True


def get_base_model():
    """
    Determine the base Model to inherit in the
    Entry Model, this allow to overload it.
    """
    if not ENTRY_BASE_MODEL:
        return EntryAbstractClass

    dot = ENTRY_BASE_MODEL.rindex('.')
    module_name = ENTRY_BASE_MODEL[:dot]
    class_name = ENTRY_BASE_MODEL[dot + 1:]
    try:
        _class = getattr(import_module(module_name), class_name)
        return _class
    except (ImportError, AttributeError):
        warnings.warn('%s cannot be imported' % ENTRY_BASE_MODEL,
                      RuntimeWarning)
    return EntryAbstractClass


class Entry(get_base_model()):
    """
    The final Entry model based on inheritence.

    Check this out for customizing the Entry Model class:
    http://django-blog-zinnia.rtfd.org/extending-entry
    """
