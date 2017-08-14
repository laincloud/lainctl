import re

def regex_match(patten, input):
    regex = re.compile(patten)
    match = regex.match(input)
    if match is not None:
        return match.groups()