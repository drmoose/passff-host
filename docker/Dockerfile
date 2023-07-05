ARG FROM=docker.io/library/debian:latest
FROM $FROM

ADD setup.sh /
RUN set -xe \
  ; chmod +x /setup.sh \
  ; /setup.sh \
  ; rm /setup.sh \
  ; true

# If gnupg help doesn't have examples in spanish, we have failed to set up
# locales, so stop here rather than waiting for the rest of the dockerfile to
# complete.
RUN eval $(LC_ALL=es_ES.UTF-8 LANG=es_ES.UTF-8 LANGUAGE=es locale) gpg --help \
  | grep Ejemplos:

ADD gpg_init.sh /
RUN set -xe \
  ; chmod +x /gpg_init.sh \
  ; /gpg_init.sh \
  ; rm /gpg_init.sh \
  ; true

ADD pinmode /bin
RUN chmod +x /bin/pinmode

# A few more asserts make sure the test harness works correctly...
RUN test f"$(pinmode loopback pass test)" = f"hello world"
RUN ! PASSWORD_STORE_GPG_OPTS=--status-fd=2 pinmode loopback pass unreadable
RUN ! PASSWORD_STORE_GPG_OPTS=--status-fd=2 pinmode cancel pass test
RUN ! PASSWORD_STORE_GPG_OPTS=--status-fd=2 pinmode error pass test
RUN ! PASSWORD_STORE_GPG_OPTS=--status-fd=2 pinmode wrong pass test
RUN hash python3
