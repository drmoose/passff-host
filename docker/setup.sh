#!/bin/bash

set -xeo pipefail

# Configure two non-english locales for which we know gpg has translations
#   es, to get something using latin characters
#   jp, to get something using non-latin characters
test_locales=(
  es_ES.UTF-8
  ja_JP.UTF-8
  en_US.UTF-8
)

if grep debian /etc/os-release; then
  export DEBIAN_FRONTEND=noninteractive
  rm -vf /etc/apt/apt.conf.d/docker-no-languages
  apt-get update

  if ! apt-get install --no-install-recommends -yqq python3; then
    # Currently broken on sid. See
    # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1040316
    sed -i 's:import importlib:&.util:' /usr/share/python3/debpython/interpreter.py
    apt-get -f install
  fi
  apt-get install -yqq pass locales

  for locale in "${test_locales[@]}"; do
    if grep -i ubuntu /etc/os-release; then
      apt-get install -y language-pack-"${locale%%_*}"
    else
      sed -i "/$locale/s:^# ::g" /etc/locale.gen
      locale-gen --no-purge "$locale"
    fi
  done
  dpkg-reconfigure locales
  apt-get install --reinstall -y gnupg
elif grep -E 'rhel|fedora' /etc/os-release; then
  # Redhat-derived containers really *really* don't want you to have locales
  rm -vf /etc/rpm/macros.image-language-conf /etc/yum/pluginconf.d/langpacks.conf
  if grep override_install_langs /etc/yum.conf; then
    sed -i '/override_install_langs/d' /etc/yum.conf
    yum -y reinstall glibc-common
  fi
  # newer redhat flavors also need...
  yum install -y glibc-locale-source || true

  yum install -y python3 gnupg
  # because if it was installed by default, it had locales stripped...
  yum reinstall -y gnupg

  if ! yum install -y pass; then
    # Most rhel versions don't have a pass package, so install dependencies
    yum install -y bash git make
  fi

  for locale in "${test_locales[@]}"; do
    localedef -c -i "${locale%%.*}" -f "${locale##*.}" "$locale"
  done
else
  cat /etc/os-release >&2
  echo "Unsupported OS." >&2
  exit 2
fi

# Above, we tried to install pass from a package, but if this distro doesn't
# have one, we'll need to install from source instead.
if ! hash pass ; then
  cd $(mktemp -d)
  git clone https://git.zx2c4.com/password-store
  cd password-store
  make install
fi
