#!/usr/bin/env node

const {searchFiles} = require('../../scripts/common')

const fs = require('fs')

process.chdir('third_party/abseil-cpp')
const files = searchFiles('absl', ['.cc', '.h']).sort()
const sources = files.filter(shouldInclude)
const internals = sources.filter(s => s.includes('internal'))
const externals = sources.filter(s => !s.includes('internal'))

const inc_files = searchFiles('absl', '.inc')

fs.writeFileSync('filenames.gni',
                 'absl_inc_files = ' + toGNArray(inc_files) + '\n' +
                 'absl_internals = ' + toGNArray(internals) + '\n' +
                 'absl_externals = ' + toGNArray(externals))

function shouldInclude(f) {
  return !f.includes('test') &&
         !f.includes('benchmark') &&
         !f.includes('mock') &&
         !f.includes('/random/') &&
         !f.endsWith('/bad_any_cast.cc') &&
         !f.endsWith('/print_hash_of.cc')
}

function toGNArray(a) {
  const arr = a.map(s => '  "' + s + '",\n')
  return '[\n' + arr.join('') + ']\n'
}
