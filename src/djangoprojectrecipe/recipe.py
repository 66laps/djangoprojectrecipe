# -*- coding: utf-8 -*-
from random import choice
import os
import subprocess
import urllib2
import shutil
import logging
import re
import codecs

from zc.buildout import UserError
import zc.recipe.egg
import setuptools

script_template = {
    'wsgi': '''

%(relative_paths_setup)s
import sys
sys.path[0:0] = [
  %(path)s,
  ]
%(initialization)s
import %(module_name)s

application = %(module_name)s.%(attrs)s(%(arguments)s)
''',
    'fcgi': '''

%(relative_paths_setup)s
import sys
sys.path[0:0] = [
  %(path)s,
  ]
%(initialization)s
import %(module_name)s

%(module_name)s.%(attrs)s(%(arguments)s)
'''
}


settings_template = '''
import os

ADMINS = (
    # ('Your Name', 'your_email@domain.com'),
)

MANAGERS = ADMINS

DATABASE_ENGINE = 'sqlite3'    # 'postgresql_psycopg2', 'postgresql', 'mysql', 'sqlite3' or 'oracle'.
DATABASE_NAME = '%(project)s.db'
DATABASE_USER = ''             # Not used with sqlite3.
DATABASE_PASSWORD = ''         # Not used with sqlite3.
DATABASE_HOST = ''             # Set to empty string for localhost. Not used with sqlite3.
DATABASE_PORT = ''             # Set to empty string for default. Not used with sqlite3.

TIME_ZONE = 'America/Chicago'

LANGUAGE_CODE = 'en-us'

# Absolute path to the directory that holds media.
# Example: "/home/media/media.lawrence.com/"
MEDIA_ROOT = %(media_root)s

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash if there is a path component (optional in other cases).
# Examples: "http://media.lawrence.com", "http://example.com/media/"
MEDIA_URL = '/media/'

# URL prefix for admin media -- CSS, JavaScript and images. Make sure to use a
# trailing slash.
# Examples: "http://foo.com/media/", "/media/".
ADMIN_MEDIA_PREFIX = '/admin_media/'

# Don't share this with anybody.
SECRET_KEY = '%(secret)s'

MIDDLEWARE_CLASSES = (
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.middleware.doc.XViewMiddleware',
)

ROOT_URLCONF = '%(urlconf)s'


INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.admin',
)

TEMPLATE_LOADERS = (
    'django.template.loaders.filesystem.load_template_source',
    'django.template.loaders.app_directories.load_template_source',
)

TEMPLATE_DIRS = (
    os.path.join(os.path.dirname(__file__), "templates"),
)


'''

site_settings_part_template = u'''# auto generated site settings for %(project)s %(site_name)s
# anything between START and END will be replaced on every buildout
from %(settings_switcher_module)s import *
SITE_ID = %(site_id)s
CACHE_MIDDLEWARE_KEY_PREFIX = '%(project)s-' + PROJECT_STAGE + '-%(site_name)s-'
'''

settings_switcher_template = '''# auto generated switcher file (imports the settings defined in the django reicpe
# ignore this file from vcs to prevent unnecessary conflicts
from %(settings)s import *
PROJECT_STAGE = '%(project_stage)s'
'''

site_settings_template = u'''# -*- coding: utf-8 -*-
# -- GENERATED START
''' + site_settings_part_template + u'''# -- GENERATED END

LANGUAGE_CODE='de'
CMS_LANGUAGES = (
  ('de', u'deutsch'),
  ('en', u'english'),
  #('fr', u'français'),
  #('it', u'italiano'),
  #('gr', u'ελληνικά'),
  #('ro', u'română'),
  #('hu', u'magyar'),
  #('bo', u'bosanski'),
  #('sr', u'српски'),
)
'''

production_settings = '''
from %(project)s.settings import *
'''

development_settings = '''
from %(project)s.settings import *
DEBUG=True
TEMPLATE_DEBUG=DEBUG
'''

urls_template = '''
from django.conf.urls.defaults import patterns, include, handler500
from django.conf import settings
from django.contrib import admin
admin.autodiscover()

handler500 # Pyflakes

urlpatterns = patterns(
    '',
    (r'^admin/(.*)', admin.site.root),
    (r'^accounts/login/$', 'django.contrib.auth.views.login'),
)

if settings.DEBUG:
    urlpatterns += patterns('',
        (r'^media/(?P<path>.*)$', 'django.views.static.serve',
         {'document_root': settings.MEDIA_ROOT}),
    )
'''

class Recipe(object):
    def __init__(self, buildout, name, options):
        self.log = logging.getLogger(name)
        self.egg = zc.recipe.egg.Egg(buildout, options['recipe'], options)

        self.buildout, self.name, self.options = buildout, name, options
        options['location'] = os.path.join(
            buildout['buildout']['parts-directory'], name)
        options['bin-directory'] = buildout['buildout']['bin-directory']

        options.setdefault('src-dir', '')
        options.setdefault('project', 'project')
        options.setdefault('project_stage', 'undefined')
        options.setdefault('settings', '%s.settings.development_local' % options['project'])
        
        options.setdefault('sites', 'sites')
        
        options.setdefault('urlconf', options['project'] + '.urls')
        options.setdefault(
            'media_root',
            "os.path.join(os.path.dirname(__file__), 'media')")
        # Set this so the rest of the recipe can expect the values to be
        # there. We need to make sure that both pythonpath and extra-paths are
        # set for BBB.
        if 'extra-paths' in options:
            options['pythonpath'] = options['extra-paths']
        else:
            options.setdefault('extra-paths', options.get('pythonpath', ''))

        # Usefull when using archived versions
        buildout['buildout'].setdefault(
            'download-cache',
            os.path.join(buildout['buildout']['directory'],
                         'downloads'))
        
        options.setdefault('control-script', self.name)
        # mod_wsgi support script
        options.setdefault('wsgi', 'false')
        options.setdefault('fcgi', 'false')
        options.setdefault('wsgilog', '')
        options.setdefault('logfile', '')

        # only try to download stuff if we aren't asked to install from cache
        self.install_from_cache = self.buildout['buildout'].get(
            'install-from-cache', '').strip() == 'true'
    
    def base_dir(self):
        return self.buildout['buildout']['directory']
    def project_dir(self):
        return os.path.join(self.base_dir(), self.options['src-dir'], self.options['project'])

    def install(self):
        location = self.options['location']
        base_dir = self.base_dir()
        
        
        project_dir = self.project_dir()

        download_dir = self.buildout['buildout']['download-cache']
        if not os.path.exists(download_dir):
            os.mkdir(download_dir)

        version = self.options['version']
        # Remove a pre-existing installation if it is there
        if os.path.exists(location):
            shutil.rmtree(location)

        if self.is_svn_url(version):
            self.install_svn_version(version, download_dir, location,
                                     self.install_from_cache)
        else:
            tarball = self.get_release(version, download_dir)
            # Extract and put the dir in its proper place
            self.install_release(version, download_dir, tarball, location)

        self.options['setup'] = location
        development = zc.recipe.egg.Develop(self.buildout,
                                            self.options['recipe'],
                                            self.options)
        development.install()
        del self.options['setup']

        extra_paths = self.get_extra_paths()
        requirements, ws = self.egg.working_set(['djangoprojectrecipe'])

        script_paths = []
        
        # Create the Django management script
        script_paths.extend(self.create_manage_script(extra_paths, ws))

        # Create the test runner
        script_paths.extend(self.create_test_runner(extra_paths, ws))

        # Make the wsgi and fastcgi scripts if enabled
        script_paths.extend(self.make_scripts(extra_paths, ws))
        
        # Create default settings if we haven't got a project
        # egg specified, and if it doesn't already exist
        if not self.options.get('projectegg'):
            if not os.path.exists(project_dir):
                self.create_project(project_dir)
            else:
                self.log.info(
                    'Skipping creating of project: %(project)s since '
                    'it exists' % self.options)
                # Make the site settings, if enabled
                self.make_site_settings()
        return script_paths + [location]

    def install_svn_version(self, version, download_dir, location,
                            install_from_cache):
        svn_url = self.version_to_svn(version)
        download_location = os.path.join(
            download_dir, 'django-' +
            self.version_to_download_suffix(version))
        if not install_from_cache:
            if os.path.exists(download_location):
                if self.svn_update(download_location, version):
                    raise UserError(
                        "Failed to update Django; %s. "
                        "Please check your internet connection." % (
                            download_location))
            else:
                self.log.info("Checking out Django from svn: %s" % svn_url)
                cmd = 'svn co %s %s' % (svn_url, download_location)
                if not self.buildout['buildout'].get('verbosity'):
                    cmd += ' -q'
                if self.command(cmd):
                    raise UserError("Failed to checkout Django. "
                                    "Please check your internet connection.")
        else:
            self.log.info("Installing Django from cache: " + download_location)

        shutil.copytree(download_location, location)


    def install_release(self, version, download_dir, tarball, destination):
        extraction_dir = os.path.join(download_dir, 'django-archive')
        setuptools.archive_util.unpack_archive(tarball, extraction_dir)
        # Lookup the resulting extraction dir instead of guessing it
        # (Django releases have a tendency not to be consistend here)
        untarred_dir = os.path.join(extraction_dir,
                                    os.listdir(extraction_dir)[0])
        shutil.move(untarred_dir, destination)
        shutil.rmtree(extraction_dir)

    def get_release(self, version, download_dir):
        tarball = os.path.join(download_dir, 'django-%s.tar.gz' % version)

        # Only download when we don't yet have an archive
        if not os.path.exists(tarball):
            download_url = 'http://www.djangoproject.com/download/%s/tarball/'
            self.log.info("Downloading Django from: %s" % (
                    download_url % version))

            tarball_f = open(tarball, 'wb')
            f = urllib2.urlopen(download_url % version)
            tarball_f.write(f.read())
            tarball_f.close()
            f.close()
        return tarball

    def create_manage_script(self, extra_paths, ws):
        project = self.options.get('projectegg', self.options['project'])
        # create the startscripts for the sites:
        site_configs = self.get_site_configs()
        site_configs['main']=self.get_main_site_config()
        scripts = []
        for name, site_config in site_configs.items():
            scripts.extend(
                zc.buildout.easy_install.scripts(
                    [(site_config['control-script'],
                      'djangoprojectrecipe.manage', 'main')],
                    ws, self.options['executable'], self.options['bin-directory'],
                    extra_paths = extra_paths,
                    arguments= "'%s'" % (site_config['settings_module']))
            )
        return scripts


    def create_test_runner(self, extra_paths, working_set):
        apps = self.options.get('test', '').split()
        # Only create the testrunner if the user requests it
        if apps:
            scripts = []
            return zc.buildout.easy_install.scripts(
                [(self.options.get('testrunner', 'test'),
                  'djangoprojectrecipe.test', 'main')],
                working_set, self.options['executable'],
                self.options['bin-directory'],
                extra_paths = extra_paths,
                arguments= "'%s', %s" % (
                    self.options['settings'],
                    ', '.join(["'%s'" % app for app in apps])))
        else:
            return []


    def create_project(self, project_dir):
        os.makedirs(project_dir)

        template_vars = {'secret': self.generate_secret()}
        template_vars.update(self.options)

        self.create_file(
            os.path.join(project_dir, 'development.py'),
            development_settings, template_vars)

        self.create_file(
            os.path.join(project_dir, 'production.py'),
            production_settings, template_vars)

        self.create_file(
            os.path.join(project_dir, 'urls.py'),
            urls_template, template_vars)

        self.create_file(
            os.path.join(project_dir, 'settings.py'),
            settings_template, template_vars)

        # Create the media and templates directories for our
        # project
        os.mkdir(os.path.join(project_dir, 'media'))
        os.mkdir(os.path.join(project_dir, 'templates'))

        # Make the settings dir a Python package so that Django
        # can load the settings from it. It will act like the
        # project dir.
        open(os.path.join(project_dir, '__init__.py'), 'w').close()

    def make_scripts(self, extra_paths, ws):
        site_configs = self.get_site_configs()
        site_configs['main']=self.get_main_site_config()
        scripts = []
        _script_template = zc.buildout.easy_install.script_template
        for protocol in ('wsgi', 'fcgi'):
            zc.buildout.easy_install.script_template = \
                zc.buildout.easy_install.script_header + \
                    script_template[protocol]
            if self.options.get(protocol, '').lower() == 'true':
                project = self.options.get('projectegg',
                                           self.options['project'])
                for name, site_config in site_configs.items():
                    scripts.extend(
                        zc.buildout.easy_install.scripts(
                            [('%s.%s' % (site_config['control-script'],
                                         protocol),
                              'djangoprojectrecipe.%s' % protocol, 'main')],
                            ws,
                            self.options['executable'],
                            self.options['bin-directory'],
                            extra_paths=extra_paths,
                            arguments= "'%s', logfile='%s'" % (
                                site_config['settings_module'],
                                self.options.get('logfile'))))
        zc.buildout.easy_install.script_template = _script_template
        return scripts

    def is_svn_url(self, version):
        # Search if there is http/https/svn or svn+[a tunnel identifier] in the
        # url or if the trunk marker is used, all indicating the use of svn
        svn_version_search = re.compile(
            r'^(http|https|svn|svn\+[a-zA-Z-_]+)://|^(trunk)$').search(version)
        return svn_version_search is not None

    def version_to_svn(self, version):
        if version == 'trunk':
            return 'http://code.djangoproject.com/svn/django/trunk/'
        else:
            return version

    def version_to_download_suffix(self, version):
        if version == 'trunk':
            return 'svn'
        return [p for p in version.split('/') if p][-1]

    def svn_update(self, path, version):
        command = 'svn up'
        revision_search = re.compile(r'@([0-9]*)$').search(
            self.options['version'])

        if revision_search is not None:
            command += ' -r ' + revision_search.group(1)
        self.log.info("Updating Django from svn")
        if not self.buildout['buildout'].get('verbosity'):
            command += ' -q'
        return self.command(command, cwd=path)

    def get_extra_paths(self):
        extra_paths = [self.options['location'],
                       self.buildout['buildout']['directory']
                       ]
        if self.options['src-dir']: extra_paths.append(self.options['src-dir'])

        # Add libraries found by a site .pth files to our extra-paths.
        if 'pth-files' in self.options:
            import site
            for pth_file in self.options['pth-files'].splitlines():
                pth_libs = site.addsitedir(pth_file, set())
                if not pth_libs:
                    self.log.warning(
                        "No site *.pth libraries found for pth_file=%s" % (
                         pth_file,))
                else:
                    self.log.info("Adding *.pth libraries=%s" % pth_libs)
                    self.options['extra-paths'] += '\n' + '\n'.join(pth_libs)

        pythonpath = [p.replace('/', os.path.sep) for p in
                      self.options['extra-paths'].splitlines() if p.strip()]

        extra_paths.extend(pythonpath)
        return extra_paths

    def update(self):
        newest = self.buildout['buildout'].get('newest') != 'false'
        if newest and not self.install_from_cache and \
                self.is_svn_url(self.options['version']):
            self.svn_update(self.options['location'], self.options['version'])

        extra_paths = self.get_extra_paths()
        requirements, ws = self.egg.working_set(['djangoprojectrecipe'])
        # Create the Django management script
        self.create_manage_script(extra_paths, ws)

        # Create the test runner
        self.create_test_runner(extra_paths, ws)

        # Make the wsgi and fastcgi scripts if enabled
        self.make_scripts(extra_paths, ws)
        
        # Make the site settings, if enabled
        self.make_site_settings()

    def command(self, cmd, **kwargs):
        output = subprocess.PIPE
        if self.buildout['buildout'].get('verbosity'):
            output = None
        command = subprocess.Popen(
            cmd, shell=True, stdout=output, **kwargs)
        return command.wait()

    def create_file(self, file, template, options, overwrite=False):
        if not overwrite and os.path.exists(file):
            return

        f = open(file, 'w')
        f.write(template % options)
        f.close()
    
    def update_or_create_site_settings_file(self, site_options):
        output = []
        file = site_options['settings_file']
        folder = os.path.dirname(file)
        init_file = os.path.join(folder, '__init__.py')
        if not os.path.exists(folder):
            os.makedirs(folder)
        if not os.path.exists(init_file):
            self.create_file(init_file, '', {})
        if os.path.exists(file):
            print "     updating site settings for %s" % site_options['site_name']
            input = codecs.open(file, 'r', 'utf-8').readlines()
            is_generated = False
            for line in input:
                #line = line.strip('\n')
                if line.startswith('# -- GENERATED START'):
                    is_generated = True
                    output.append(line)
                elif line.startswith('# -- GENERATED END'):
                    is_generated = False
                    output.append(site_settings_part_template % site_options)
                    output.append(line)
                else:
                    if is_generated == True:
                        pass
                    else:
                        output.append(line)
        else:
            print "     creating site settings for %s" % site_options['site_name']
            output = site_settings_template % site_options

        f = codecs.open(file, 'w', 'utf-8')
        f.write("".join(output))
        f.close()

    def generate_secret(self):
        chars = 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'
        return ''.join([choice(chars) for i in range(50)])
    
    def make_site_settings(self):
        project_dir = self.project_dir()
        self.create_file(
                os.path.join(self.project_dir(), 'settings/switcher.py'), 
                settings_switcher_template, 
                {'settings':self.options['settings'],'project_stage': self.options['project_stage']},
                overwrite=True)
        sites = self.get_site_configs()
        for name, config in sites.items():
            config['site_name'] = name
            config['project'] = self.options['project']
            config['settings'] = self.options['settings']
            self.update_or_create_site_settings_file(
                    config )
    def get_site_configs(self):
        '''
        get the list of sites and the SITE_ID's
        '''
        sites_section = self.options['sites']
        section = self.buildout.get(sites_section, {})
        sites = {}
        for name, value in section.items():
            site_id = value #add more options here later
            sites[name]={
                'site_id': value, 
                'site_name': name,
                'settings_module':'%s.settings.sites.%s' % (self.options['project'], name),
                'settings_switcher_module': '%s.settings.switcher' % (self.options['project'],),
                'settings_file':os.path.join(self.project_dir(), 'settings/sites/%s.py' % name),
                'control-script':'%s.%s' % (self.options['control-script'], name)
                }
        return sites
    def get_main_site_config(self):
        return {
            'site_name': None,
            'settings_module':'%s' % (self.options['settings'],),
            'control-script':'%s' % (self.options['control-script'],),
        }
            