# T3 Foundation Gemstone Project [t3gemstone.org]
# SPDX-License-Identifier: Apache-2.0
#
# Build environment of the Camera ISP SDK. 'task box' builds this image and enters it
# through Distrobox; CI runs its tasks in it too.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Overlays (cpp, dtc), descriptor parser tests (gcc, GLib), the plugin patch
# check (git), and out-of-tree kernel drivers (make, the aarch64 cross
# compiler and the kernel build's host tools)
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
        bash-completion \
        bc \
        bison \
        build-essential \
        ca-certificates \
        cpp \
        curl \
        device-tree-compiler \
        file \
        flex \
        gcc-aarch64-linux-gnu \
        git \
        kmod \
        libelf-dev \
        libglib2.0-dev \
        libssl-dev \
        locales \
        make \
        nano \
        openssh-client \
        pkg-config \
        python3 \
        sudo \
        vim \
        xz-utils \
    && locale-gen en_US.UTF-8

# Distrobox copies /etc/os-release out of the image; the symlink would not survive
RUN rm -f /etc/os-release && cp /usr/lib/os-release /etc/os-release

# Allow minimum password length of image in Distrobox to be 1 character
RUN sed -i 's/pam_unix\.so obscure/pam_unix.so minlen=1 obscure/' /etc/pam.d/common-password
RUN echo gemstone > /etc/hostname

# Taskfile Installation
RUN curl --location https://github.com/go-task/task/releases/download/v3.50.0/task_3.50.0_linux_amd64.deb --output /tmp/task.deb && \
    apt-get install -y /tmp/task.deb && \
    rm /tmp/task.deb && \
    task --completion bash > /etc/bash_completion.d/task

CMD ["bash"]
