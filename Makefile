# Makefile for Pico-de-GIME

LIB_DIR = lib/microdot
BASE_URL = https://raw.githubusercontent.com/miguelgrinberg/microdot/main/src/microdot

.PHONY: all install clean

all: install

install:
	@echo "Creating library directory..."
	@mkdir -p $(LIB_DIR)
	@echo "Downloading Microdot dependencies..."
	@curl -s $(BASE_URL)/__init__.py -o $(LIB_DIR)/__init__.py
	@curl -s $(BASE_URL)/websocket.py -o $(LIB_DIR)/websocket.py
	@curl -s $(BASE_URL)/cors.py -o $(LIB_DIR)/cors.py
	@echo "✅ Dependencies installed in $(LIB_DIR)"
	@echo "You can now copy the 'lib' folder to your Pico W."

clean:
	@rm -rf lib
	@echo "Library folder removed."
