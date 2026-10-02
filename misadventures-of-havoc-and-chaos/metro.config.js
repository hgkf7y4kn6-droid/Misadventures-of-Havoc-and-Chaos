// NativeWind v5: compiles Tailwind CSS (src/global.css) into React Native styles at build time.
const { getDefaultConfig } = require('expo/metro-config');
const { withNativewind } = require('nativewind/metro');

module.exports = withNativewind(getDefaultConfig(__dirname));
