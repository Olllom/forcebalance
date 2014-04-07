import os
import re
import csv
import copy
import errno
import numpy as np
import pandas as pd
import itertools
import cStringIO

from forcebalance.observable import *
from forcebalance.target import Target
from forcebalance.finite_difference import in_fd
from forcebalance.nifty import flat, col, row, isint
from forcebalance.nifty import lp_dump, lp_load, wopen, _exec
from forcebalance.nifty import LinkFile, link_dir_contents
from forcebalance.nifty import printcool, printcool_dictionary

from collections import defaultdict, OrderedDict

import forcebalance
from forcebalance.output import *
logger = getLogger(__name__)
# print logger.parent.parent.handlers[0]
# logger.parent.parent.handlers = []

class TextParser(object):
    """ Parse a text file. """
    def __init__(self, fnm):
        self.fnm = fnm
        self.parse()

    def is_empty_line(self):
        return all([len(fld.strip()) == 0 for fld in self.fields])

    def is_comment_line(self):
        return re.match('^[\'"]?#',self.fields[0].strip())

    def process_header(self):
        """ Function for setting more attributes using the header line, if needed. """
        self.heading = [i.strip() for i in self.fields[:]]

    def process_data(self):
        """ Function for setting more attributes using the current line, if needed. """
        trow = []
        for ifld in range(len(self.heading)):
            if ifld < len(self.fields):
                trow.append(self.fields[ifld].strip())
            else:
                trow.append('')
        return trow

    def sanity_check(self):
        """ Extra sanity checks. """

    def parse(self):
        self.heading = []                 # Fields in header line
        meta = defaultdict(list)          # Dictionary of metadata
        found_header = 0                  # Whether we found the header line
        table = []                        # List of data records
        self.generate_splits()            # Generate a list of records for each line.
        self.ln = 0                       # Current line number
        for line, fields in zip(open(self.fnm).readlines(), self.splits):
            # Set attribute so methods can use it.
            self.fields = fields
            # Skip over empty lines or comment lines.
            if self.is_empty_line():
                logger.debug("\x1b[96mempt\x1b[0m %s\n" % line.replace('\n',''))
                self.ln += 1
                continue
            if self.is_comment_line():
                logger.debug("\x1b[96mcomm\x1b[0m %s\n" % line.replace('\n',''))
                self.ln += 1
                continue
            # Indicates metadata mode.
            is_meta = 0
            # Indicates whether this is the header line.
            is_header = 0
            # Split line by tabs.
            for ifld, fld in enumerate(fields):
                fld = fld.strip()
                # Stop parsing when we encounter a comment line.
                if re.match('^[\'"]?#',fld): break
                # The first word would contain the name of the metadata key.
                if ifld == 0:
                    mkey = fld
                # Check if the first field is an equals sign (turn on metadata mode).
                if ifld == 1:
                    # Activate metadata mode.
                    if fld == "=":
                        is_meta = 1
                    # Otherwise, this is the header line.
                    elif not found_header:
                        is_header = 1
                        found_header = 1
                # Read in metadata.
                if ifld > 1 and is_meta:
                    meta[mkey].append(fld)
            # Set field start, field end, and field content for the header.
            if is_header:
                logger.debug("\x1b[1;96mhead\x1b[0m %s\n" % line.replace('\n',''))
                self.process_header()
            elif is_meta:
                logger.debug("\x1b[96mmeta\x1b[0m %s\n" % line.replace('\n',''))
            else:
                # Build the row of data to be appended to the table.
                # Loop through the fields in the header and inserts fields
                # in the data line accordingly.  Ignores trailing tabs/spaces.
                logger.debug("\x1b[96mdata\x1b[0m %s\n" % line.replace('\n',''))
                table.append(self.process_data())
            self.ln += 1
        self.sanity_check()
        if logger.level == DEBUG:
            printcool("%s parsed as %s" % (self.fnm.replace(os.getcwd()+'/',''), self.format), color=6)
        self.metadata = meta
        self.table = table
        
class CSV_Parser(TextParser):
    
    """ 
    Parse a comma-separated file.  This class is for all
    source files that are .csv format (characterized by having the
    same number of comma-separated fields in each line).  Fields are
    separated by commas but they may contain commas as well.

    In contrast to the other formats, .csv MUST contain the same
    number of commas in each line.  .csv format is easily prepared
    using Excel.
    """
    
    def __init__(self, fnm):
        self.format = "comma-separated values (csv)"
        super(CSV_Parser, self).__init__(fnm)

    def generate_splits(self):
        with open(self.fnm, 'r') as f: self.splits = list(csv.reader(f))

class TAB_Parser(TextParser):
    
    """ 
    Parse a tab-delimited file.  This function is called for all
    source files that aren't csv and contain at least one tab.  
    Fields are separated by tabs and do not contain tabs.

    Tab-delimited format is easy to prepare using programs like Excel.
    It is easier to read than .csv but represented differently by
    different editors.  
    
    Empty fields must still exist (represented using multiple tabs).
    """
    
    def __init__(self, fnm):
        self.format = "tab-delimited text"
        super(TAB_Parser, self).__init__(fnm)

    def generate_splits(self):
        self.splits = [line.split('\t') for line in open(self.fnm).readlines()]

class FIX_Parser(TextParser):
    
    """ 
    Parse a fixed width format file.  This function is called for all
    source files that aren't csv and contain no tabs.

    Fixed width is harder to prepare by hand but easiest to read,
    because it looks the same in all text editors.  The field width is
    determined by the header line (first line in the data table),
    i.e. the first non-empty, non-comment, non-metadata line.

    Empty fields need to be filled with the correct number of spaces.
    All fields must have the same alignment (left or right).  The
    start and end of each field is determined from the header line and
    used to determine alignment. If the alignment cannot be determined
    then it will throw an error.

    Example of a left-aligned fixed width file:

    T           P (atm)     Al          Al_wt       Scd1_idx    Scd1        Scd2_idx    Scd2    
    323.15      1           0.631       1           C15                     C34                 
                                                    C17         0.198144    C36         0.198144
                                                    C18         0.198128    C37         0.198128
                                                    C19         0.198111    C38         0.198111
                                                    C20         0.198095    C39         0.198095
                                                    C21         0.198079    C40         0.198079
                                                    C22         0.197799    C41         0.197537
                                                    C23         0.198045    C42         0.198046
                                                    C24         0.178844    C43         0.178844
                                                    C25         0.167527    C44         0.178565
                                                    C26         0.148851    C45         0.16751
                                                    C27         0.134117    C46         0.148834
                                                    C28         0.119646    C47         0.1341
                                                    C29         0.100969    C48         0.110956
                                                    C30         0.07546     C49         0.087549
                                                    C31                     C50

    """

    def __init__(self, fnm):
        self.format = "fixed-width text"
        self.fbegs_dat = []
        self.fends_dat = []
        super(FIX_Parser, self).__init__(fnm)

    def generate_splits(self):
        # This regular expression splits a string looking like this:
        # "Density (kg m^-3) Hvap (kJ mol^-1) Alpha Kappa".  But I
        # don't want to split in these places: "Density_(kg_m^-3)
        # Hvap_(kJ_mol^-1) Alpha Kappa"
        allfields = [list(re.finditer('[^\s(]+(?:\s*\([^)]*\))?', line)) for line in open(self.fnm).readlines()]
        self.splits = []
        # Field start / end positions for each line in the file
        self.fbegs = []
        self.fends = []
        for line, fields in zip(open(self.fnm).readlines(), allfields):
            self.splits.append([fld.group(0) for fld in fields])
            self.fbegs.append([fld.start() for fld in fields])
            self.fends.append([fld.end() for fld in fields])
        
    def process_header(self):
        super(FIX_Parser, self).process_header()
        # Field start / end positions for the header line
        self.hbeg = self.fbegs[self.ln]
        self.hend = self.fends[self.ln]

    def process_data(self):
        trow = []
        hbeg = self.hbeg
        hend = self.hend
        fbeg = self.fbegs[self.ln]
        fend = self.fends[self.ln]
        fields = self.fields
        # Check alignment and throw an error if incorrectly formatted.
        if not ((set(fbeg).issubset(hbeg)) or (set(fend).issubset(hend))):
            logger.error("This \x1b[91mdata line\x1b[0m is not aligned with the \x1b[92mheader line\x1b[0m!\n")
            logger.error("\x1b[92m%s\x1b[0m\n" % header.replace('\n',''))
            logger.error("\x1b[91m%s\x1b[0m\n" % line.replace('\n',''))
            raise RuntimeError
        # Left-aligned case
        if set(fbeg).issubset(hbeg):
            for hpos in hbeg:
                if hpos in fbeg:
                    trow.append(fields[fbeg.index(hpos)])
                else:
                    trow.append('')
        # Right-aligned case
        if set(fend).issubset(hend):
            for hpos in hend:
                if hpos in fend:
                    trow.append(fields[fend.index(hpos)].strip())
                else:
                    trow.append('')
        # Field start / end positions for the line of data
        self.fbegs_dat.append(fbeg[:])
        self.fends_dat.append(fend[:])
        return trow

    def sanity_check(self):
        if set(self.hbeg).issuperset(set(itertools.chain(*self.fbegs_dat))):
            self.format = "left-aligned fixed width text"
        elif set(self.hend).issuperset(set(itertools.chain(*self.fends_dat))):
            self.format = "right-aligned fixed width text"
        else:
            # Sanity check - it should never get here unless the parser is incorrect.
            logger.error("Fixed-width format detected but columns are neither left-aligned nor right-aligned!\n")
            raise RuntimeError
    
def parse1(fnm):

    """Determine the format of the source file and call the
    appropriate parsing function."""

    # CSV files have the same number of comma separated fields in every line, they are the simplest to parse.
    with open(fnm, 'r') as f: csvf = list(csv.reader(f))
    if len(csvf[0]) > 1 and len(set([len(i) for i in csvf])) == 1:
        return CSV_Parser(fnm)

    # Strip away comments and empty lines.
    nclines = [re.sub('[ \t]*#.*$','',line) for line in open(fnm).readlines() 
               if not (line.strip().startswith("#") or not line.strip())]

    # Print the sanitized lines to a new file object.
    # Note the file object needs ot be rewound every time we read or write to it.
    fdat = cStringIO.StringIO()
    for line in nclines:
        print >> fdat, line,
    fdat.seek(0)
    
    # Now the file can either be tab-delimited or fixed width.
    # If ANY tabs are found in the sanitized lines, then it is taken to be
    # a tab-delimited file.
    have_tabs = any(['\t' in line for line in fdat.readlines()]) ; fdat.seek(0)
    if have_tabs:
        return TAB_Parser(fnm)
    else:
        return FIX_Parser(fnm)
    return

def fix_suffix(obs, head, suffixs, standard_suffix):

    """ Standardize the suffix in a column heading. """

    if head in suffixs:
        if obs == '': 
            logger.error('\x1b[91mEncountered heading %s but there is no observable to the left\x1b[0m\n' % head)
            raise RuntimeError
        return obs + '_' + standard_suffix, False
    elif len(head.split('_')) > 1 and head.split('_')[-1] in suffixs:
        newhl = head.split('_')
        newhl[-1] = standard_suffix
        return '_'.join(newhl), False
    else:
        return head, True

def stand_head(head, obs):

    """ 
    Standardize a column heading.  Does the following:

    1) Make lowercase
    2) Split off the physical unit
    3) If a weight, uncertainty or atom index, prepend the observable name
    4) Shorten temperature and pressure
    5) Determine if this is a new observable
    
    Parameters:
    head = Name of the heading
    obs = Name of the observable (e.g. from a previously read field)
    """

    head = head.lower()
    usplit = re.split(' *\(', head, maxsplit=1)
    punit = ''
    if len(usplit) > 1:
        hfirst = usplit[0]
        punit = re.sub('\)$','',usplit[1].strip())
        logger.debug("header %s split into %s, %s" % (head, hfirst, punit))
    else:
        hfirst = head
    newh = hfirst
    newh, o1 = fix_suffix(obs, newh, ['w', 'wt', 'wts', 'weight', 'weights'], 'wt')
    newh, o2 = fix_suffix(obs, newh, ['s', 'sig', 'sigma', 'sigmas'], 'sig')
    newh, o3 = fix_suffix(obs, newh, ['i', 'idx', 'index', 'indices'], 'idx')
    if newh in ['t', 'temp', 'temperature']: newh = 'temp'
    if newh in ['p', 'pres', 'pressure']: newh = 'pres'
    if all([o1, o2, o3]):
        obs = newh
    if newh != hfirst:
        logger.debug("header %s renamed to %s\n" % (hfirst, newh))
    return newh, punit, obs

class Thermo(Target):
    """
    A target for fitting general experimental data sets. The source
    data is described in a text file formatted according to the
    Specification.

    """
    def __init__(self, options, tgt_opts, forcefield):
        ## Initialize base class
        super(Thermo, self).__init__(options, tgt_opts, forcefield)

        ## Parameters
        # Source data (experimental data, model parameters and weights)
        self.set_option(tgt_opts, "source", forceprint=True)
        # Observables to calculate
        self.set_option(tgt_opts, "observables", "observable_names", forceprint=True)
        # Length of simulation chain
        self.set_option(tgt_opts, "n_sim_chain", forceprint=True)
        # Number of time steps in the equilibration run
        self.set_option(tgt_opts, "eq_steps", forceprint=True)
        # Number of time steps in the production run
        self.set_option(tgt_opts, "md_steps", forceprint=True)

        ## Variables
        # Prefix names for simulation data
        self.simpfx    = "sim"
        # Data points for observables
        self.points    = []
        # Denominators for observables
        self.denoms    = {}
        # Weights for observables
        self.weights   = {}

        ## A mapping that takes us from observable names to Observable objects.
        self.Observable_Map = {'density' : Observable_Density,
                               'rho' : Observable_Density,
                               'hvap' : Observable_H_vap,
                               'h_vap' : Observable_H_vap}

        ## Read source data and initialize points; creates self.Data, self.Ensembles and self.Observables objects.
        self.read_source(os.path.join(self.root, self.tgtdir, self.source))
        
        ## Copy run scripts from ForceBalance installation directory
        for f in self.scripts:
            LinkFile(os.path.join(os.path.split(__file__)[0], "data", f),
                     os.path.join(self.root, self.tempdir, f))

        ## Set up simulations
        self.prepare_simulations()

    def read_source(self, srcfnm):
        """Read and store source data.

        Parameters
        ----------
        srcfnm : string
            Read source data from this filename.

        Returns
        -------
        Nothing

        """
            
        logger.info('Parsing source file %s\n' % srcfnm)
        source = parse1(srcfnm)
        printcool_dictionary(source.metadata, title="Metadata")
        revhead = []
        obs = ''
        obsnames = []

        units = defaultdict(str)

        for i, head in enumerate(source.heading):
            if i == 0 and head.lower() == 'index': # Treat special case because index can also mean other things
                revhead.append('index')
                continue
            newh, punit, obs = stand_head(head, obs)
            if obs not in obsnames + ['temp', 'pres', 'n_ic']: obsnames.append(obs)
            revhead.append(newh)
            if punit != '':
                units[newh] = punit
        source.heading = revhead
 
        if len(set(revhead)) != len(revhead):
            logger.error('Column headings : ' + str(revhead) + '\n')
            logger.error('\x1b[91mColumn headings are not unique!\x1b[0m\n')
            raise RuntimeError

        if revhead[0] != 'index':
            logger.error('\x1b[91mIndex column heading is not present\x1b[0m\n(Add an Index column on the left!)\n')
            raise RuntimeError
            
        uqidx = []
        saveidx = ''
        index = []
        snum = 0
        drows = []
        # thisidx = Index that is built from the current row (may be empty)
        # saveidx = Index that may have been saved from a previous row
        # snum = Subindex number
        # List of (index, heading) tuples which contain file references.
        fref = OrderedDict()
        for rn, row in enumerate(source.table):
            this_insert = []
            thisidx = row[0]
            if thisidx != '': 
                saveidx = thisidx
                snum = 0
                if saveidx in uqidx: 
                    logger.error('Index %s is duplicated in data table\n' % i)
                    raise RuntimeError
                uqidx.append(saveidx)
            index.append((saveidx, snum))
            if saveidx == '':
                logger.error('Row of data : ' + str(row) + '\n')
                logger.error('\x1b[91mThis row does not have an index!\x1b[0m\n')
                raise RuntimeError
            snum += 1
            if any([':' in fld for fld in row[1:]]):
                # Here we read rows from another data table.  
                # Other files may be referenced in the cell of a primary
                # table using filename:column_number (numbered from 1).
                # Rules: (1) No matter where the filename appears in the column,
                # the column is inserted at the beginning of the system index.
                # (2) There can only be one file per system index / column.
                # (3) The column heading in the secondary file that's being
                # referenced must match that of the reference in the primary file.
                obs2 = ''
                for cid_, fld in enumerate(row[1:]):
                    if ':' not in fld: continue
                    cid = cid_ + 1
                    def reffld_error(reason=''):
                        logger.error('Row: : ' + ' '.join(row) + '\n')
                        logger.error('Entry : ' + fld + '\n')
                        logger.error('This filename:column reference is not valid!%s' % 
                                     (' (%s)' % reason if reason != '' else ''))
                        raise RuntimeError
                    if len(fld.split(':')) != 2:
                        reffld_error('Wrong number of colon-separated fields')
                    if not isint(fld.split(':')[1]):
                        reffld_error('Must be an integer after the colon')
                    fnm = fld.split(':')[0]
                    fcol_ = int(fld.split(':')[1])
                    fpath = os.path.join(os.path.split(srcfnm)[0], fnm)
                    if not os.path.exists(fpath):
                        reffld_error('%s does not exist' % fpath)
                    if (saveidx, revhead[cid]) in fref:
                        reffld_error('%s already contains a file reference' % (saveidx, revhead[cid]))
                    subfile = parse1(fpath)
                    fcol = fcol_ - 1
                    head2, punit2, obs2 = stand_head(subfile.heading[fcol], obs2)
                    if revhead[cid] != head2:
                        reffld_error("Column heading of %s (%s) doesn't match original (%s)" % (fnm, head2, revhead[cid]))
                    fref[(saveidx, revhead[cid])] = [row2[fcol] for row2 in subfile.table]

        # Insert the file-referenced data tables appropriately into
        # our main data table.
        for (saveidx, head), newcol in fref.items():
            inum = 0
            for irow in range(len(source.table)):
                if index[irow][0] != saveidx: continue
                lrow = irow
                cidx = revhead.index(head)
                source.table[irow][cidx] = newcol[inum]
                inum += 1
                if inum >= len(newcol): break
            for inum1 in range(inum, len(newcol)):
                lrow += 1
                nrow = ['' for i in range(len(revhead))]
                nrow[cidx] = newcol[inum1]
                source.table.insert(lrow, nrow)
                index.insert(lrow, (saveidx, inum1))
                
        for rn, row in enumerate(source.table):
            drows.append([i if i != '' else np.nan for i in row[1:]])

        # Turn it into a pandas DataFrame.
        self.Data = pd.DataFrame(drows, columns=revhead[1:], index=pd.MultiIndex.from_tuples(index, names=['ensemble', 'subindex']))

        # A list of ensembles (i.e. top-level indices) which correspond
        # to sets of simulations that we'll be running.
        self.Ensembles = []
        for idx in self.Data.index:
            if idx[0] not in self.Ensembles:
                self.Ensembles.append(idx[0])

        # A list of Observable objects (i.e. column headings) which
        # contain methods for calculating observables that we need.
        # Think about: 
        # (1) How much variability is allowed across Ensembles?
        #     For instance, different S_cd is permissible.
        self.Observables = OrderedDict()
        for obsname in [stand_head(i, '')[2] for i in self.observable_names]:
            if obsname in self.Observables:
                logger.error('%s was already specified as an observable' % (obsname))
            self.Observables[obsname] = OrderedDict()
            for ie, ensemble in enumerate(self.Ensembles):
                if obsname in self.Observable_Map:
                    newobs = self.Observable_Map[obsname](source=self.Data.ix[ensemble])
                    logger.info('%s is specified as an observable, appending %s class\n' % (obsname, newobs.__class__.__name__))
                    self.Observables[obsname][ensemble] = newobs
                else:
                    logger.warn('%s is specified but there is no corresponding Observable class, appending empty one\n' % obsname)
                    self.Observables[obsname][ensemble] = Observable(name=obsname, source=self.Data.ix[ensemble])

        # for ensemble in self.Ensembles:
        #     self.Observables[ensemble] = []
        # for obsname in obsnames:
        #     for ensemble, ie in enumerate(self.Ensembles):
        #         if obsname in self.Observable_Map:
        #             newobs = self.Observable_Map[obsname](source=self.Data.ix[ensemble])
        #             if newobs.name in [obs.name for obs in self.Observables[ensemble]]:
        #                 logger.error('%s is specified but a %s observable already exists' % (obsname, newobs.__class__.__name__))
        #             logger.info('%s is specified as an observable, appending %s class\n' % (obsname, newobs.__class__.__name__))
        #             self.Observables[ensemble].append(newobs)
        #         else:
        #             logger.warn('%s is specified but there is no corresponding Observable class, appending empty one\n' % obsname)
        #             self.Observables[ensemble].append(Observable(name=obsname, source=self.Data.ix[ensemble]))
        return

    def determine_simulations(self):

        """ 
        Determine which simulations need to be run.  The same
        simulations are run for each ensemble in the data set.
        """

        # Determine which simulations are needed.
        sreqs = OrderedDict()
        for obsname in self.Observables:
            sreqs[obsname] = self.Observables[obsname][self.Ensembles[0]].sreq

        def narrow():
            # Get the names of simulations that are REQUIRED to calculate the observables.
            toplevel = list(itertools.chain(*[[j for j in sreqs[i] if type(j) == str] for i in sreqs]))
            # Whoa, this is a deeply nested loop.  What does it do?
            # First loop over the elements in "sreqs" for each observable name.
            # If the element is a string, then it's a required simulation name (top level).
            # If the element is a list, then it's a list of valid simulation names
            # and we need to narrow the list down.
            # For the ones that are lists (and have any intersection with the top level),
            # delete the ones that don't intersect.
            sreq0 = copy.deepcopy(sreqs)
            for obsname in sreqs:
                for sims in sreqs[obsname]:
                    if type(sims) == list:
                        if len(sims) == 1:
                            sreqs[obsname] = [sims[0]]
                        elif any([i in sims for i in toplevel]):
                            for j in sims:
                                if j not in toplevel: sims.remove(j)
            return sreqs != sreq0

        print sreqs
        while narrow():
            print sreqs
        # For the leftover observables where there is still some ambiguity,
        # we attempt 
        # To do: Figure this out from existing initial conditions maybe
        for obsname in sreqs:
            for sims in sreqs[obsname]:
                if type(sims) == list:
                    for sim in sims:
                        if has_ic(sim):
                            sreqs[obsname] = [sim]
                        

        self.Simulations = OrderedDict([(i, []) for i in self.Ensembles])
        
        return

    def prepare_simulations(self):

        """ 

        Prepare simulations to be launched.  Set initial conditions
        and create directories.  This function is intended to be run
        at the start of each optimization cycle, so that initial
        conditions may be easily set.

        """
        # print narrow()
            
        # The list of simulations that we'll be running.
        self.Simulations = OrderedDict([(i, []) for i in self.Ensembles])
        
        return

    def launch_simulation(self, index, simname):

        """ 

        Launch a simulation - either locally or via the Work Queue.
        This function is intended to be run within the folder:
        target_name/iteration_number/system_index/simulation_name/initial_condition OR 
        target_name/iteration_number/system_index/simulation_name
        
        """
        
        wq = getWorkQueue()
        if not (os.path.exists('result.p') or os.path.exists('result.p.bz2')):
            link_dir_contents(os.path.join(self.root,self.rundir),os.getcwd())
            self.last_traj += [os.path.join(os.getcwd(), i) for i in self.extra_output]
            self.liquid_mol[simnum%len(self.liquid_mol)].write(self.liquid_coords, ftype='tinker' if self.engname == 'tinker' else None)
            cmdstr = '%s python npt.py %s %.3f %.3f' % (self.nptpfx, self.engname, temperature, pressure)
            if wq == None:
                logger.info("Running condensed phase simulation locally.\n")
                logger.info("You may tail -f %s/npt.out in another terminal window\n" % os.getcwd())
                _exec(cmdstr, copy_stderr=True, outfnm='npt.out')
            else:
                queue_up(wq, command = cmdstr+' &> npt.out',
                         input_files = self.nptfiles + self.scripts + ['forcebalance.p'],
                         output_files = ['npt_result.p.bz2', 'npt.out'] + self.extra_output, tgt=self)
    
    # NAMES FOR OBJECTS!  

    # Timeseries: Time series of an instantaneous observable that is
    # returned by the MD simulation.

    # Observable: A thermodynamic property which can be compared to
    # experiment and possesses methods for calculating the property
    # and its derivatives.

    # State? Point? What should this be called??

        # # print revhead[1:]
        # # for rn, row in enumerate(drows):
        # #     print index[rn], row

        # # print repr(self.Data)

        # # # pd.DataFrame([OrderedDict([(head, row[i]) for i, head in revised_heading if row[i] != '']) for row in source.table])


        # # # pd.DataFrame(OrderedDict([(head,[row[i] for row in source.table]) for i, head in enumerate(revised_heading)]))
        # # # print self.Data.__repr__
        # # # raw_input()

        # # return

        # fp = open(expdata)
        
        # line         = fp.readline()
        # foundHeader  = False
        # names        = None
        # units        = None
        # label_header = None
        # label_unit   = None
        # count        = 0
        # metadata     = {}
        # while line:
        #     # Skip comments and blank lines
        #     if line.lstrip().startswith("#") or not line.strip():
        #         line = fp.readline()
        #         continue
        #     # Metadata is denoted using 
        #     if "=" in line: # Read variable
        #         param, value = line.split("=")
        #         param = param.strip().lower()
        #         metadata[param] = value
        #         # if param == "denoms":
        #         #     for e, v in enumerate(value.split()):
        #         #         self.denoms[self.observables[e]] = float(v)
        #         # elif param == "weights":
        #         #     for e, v in enumerate(value.split()):
        #         #         self.weights[self.observables[e]] = float(v)
        #     elif foundHeader: # Read exp data
        #         count      += 1
        #         vals        = line.split()
        #         label       = (vals[0], label_header, label_unit)
        #         refs        = np.array(vals[1:-2:2]).astype(float)
        #         wts         = np.array(vals[2:-2:2]).astype(float)
        #         temperature = float(vals[-2])
        #         pressure    = None if vals[-1].lower() == "none" else \
        #           float(vals[-1])
        #         dp = Point(count, label=label, refs=refs, weights=wts,
        #                    names=names, units=units,
        #                    temperature=temperature, pressure=pressure)
        #         self.points.append(dp)
        #     else: # Read headers
        #         foundHeader = True
        #         headers = zip(*[tuple(h.split("_")) for h in line.split()
        #                         if h != "w"])
        #         label_header = list(headers[0])[0]
        #         label_unit   = list(headers[1])[0]
        #         names        = list(headers[0][1:-2])
        #         units        = list(headers[1][1:-2])
        #     line = fp.readline()            
    
    def retrieve(self, dp):
        """Retrieve the molecular dynamics (MD) results and store the calculated
        observables in the Point object dp.

        Parameters
        ----------
        dp : Point
            Store the calculated observables in this point.

        Returns
        -------
        Nothing
        
        """
        abspath = os.path.join(os.getcwd(), '%d/md_result.p' % dp.idnr)

        if os.path.exists(abspath):
            logger.info('Reading data from ' + abspath + '.\n')

            vals, errs, grads = lp_load(open(abspath))

            dp.data["values"] = vals
            dp.data["errors"] = errs
            dp.data["grads"]  = grads

        else:
            msg = 'The file ' + abspath + ' does not exist so we cannot read it.\n'
            logger.warning(msg)

            dp.data["values"] = np.zeros((len(self.observables)))
            dp.data["errors"] = np.zeros((len(self.observables)))
            dp.data["grads"]  = np.zeros((len(self.observables), self.FF.np))
            
    def submit_jobs(self, mvals, AGrad=True, AHess=True):
        """This routine is called by Objective.stage() and will run before "get".
        It submits the jobs and the stage() function will wait for jobs
        to complete.

        Parameters
        ----------
        mvals : list
            Mathematical parameter values.
        AGrad : Boolean
            Switch to turn on analytic gradient.
        AHess : Boolean
            Switch to turn on analytic Hessian.

        Returns
        -------
        Nothing.
        
        """
        # Set up and run the simulation chain on all points.
        for pt in self.points:
            # Create subdir
            try:
                os.makedirs(str(pt.idnr))
            except OSError as exception:
                if exception.errno != errno.EEXIST:
                    raise            
                
            # Goto subdir
            os.chdir(str(pt.idnr))

            # Link dir contents from target subdir to current temp directory.
            for f in self.scripts:
                LinkFile(os.path.join(self.root, self.tempdir, f),
                         os.path.join(os.getcwd(), f))
                
            link_dir_contents(os.path.join(self.root, self.tgtdir,
                                           str(pt.idnr)), os.getcwd())
            
            # Dump the force field to a pickle file
            with wopen('forcebalance.p') as f:
                lp_dump((self.FF, mvals, self.OptionDict, AGrad), f)
                
            # Run the simulation chain for point.        
            cmdstr = ("%s python md_chain.py " % self.mdpfx +
                      " ".join(self.observables) + " " +
                      "--engine %s " % self.engname +
                      "--length %d " % self.n_sim_chain + 
                      "--name %s " % self.simpfx +
                      "--temperature %f " % pt.temperature +
                      "--pressure %f " % pt.pressure +
                      "--nequil %d " % self.eq_steps +
                      "--nsteps %d " % self.md_steps)
            _exec(cmdstr, copy_stderr=True, outfnm='md_chain.out')
        
            os.chdir('..')

    def indicate(self):
        """Shows optimization state."""
        return
        AGrad     = hasattr(self, 'Gp')
        PrintDict = OrderedDict()
        
        def print_item(key, physunit):
            if self.Xp[key] > 0:
                the_title = ("%s %s (%s)\n" % (self.name, key.capitalize(), physunit) +
                             "No.   Temperature  Pressure  Reference  " +
                             "Calculated +- Stddev " +
                             "   Delta    Weight    Term   ")
                    
                printcool_dictionary(self.Pp[key], title=the_title, bold=True,
                                     color=4, keywidth=15)
                
                bar = printcool(("%s objective function: % .3f%s" %
                                 (key.capitalize(), self.Xp[key],
                                  ", Derivative:" if AGrad else "")))
                if AGrad:
                    self.FF.print_map(vals=self.Gp[key])
                    logger.info(bar)

                PrintDict[key] = (("% 10.5f % 8.3f % 14.5e" %
                                   (self.Xp[key], self.Wp[key],
                                    self.Xp[key]*self.Wp[key])))

        for i, q in enumerate(self.observables):
            print_item(q, self.points[0].ref["units"][i])

        PrintDict['Total'] = "% 10s % 8s % 14.5e" % ("","", self.Objective)

        Title = ("%s Thermodynamic Properties:\n %-20s %40s" %
                 (self.name, "Property", "Residual x Weight = Contribution"))
        printcool_dictionary(PrintDict, color=4, title=Title, keywidth=31)
        return

    def objective_term(self, observable):
        """Calculates the contribution to the objective function (the term) for a
        given observable.

        Parameters
        ----------
        observable : string
            Calculate the objective term for this observable.

        Returns
        -------
        term : dict
            `term` is a dict with keys `X`, `G`, `H` and `info`. The values of
            these keys are the objective term itself (`X`), its gradient (`G`),
            its Hessian (`H`), and an OrderedDict with print information on
            individiual data points (`info`).
            
        """
        Objective = 0.0
        Gradient  = np.zeros(self.FF.np)
        Hessian   = np.zeros((self.FF.np, self.FF.np))

        # Grab ref data for observable        
        qid       = self.observables.index(observable)
        Exp       = np.array([pt.ref["refs"][qid] for pt in self.points])
        Weights   = np.array([pt.ref["weights"][qid] for pt in self.points])
        Denom     = self.denoms[observable]
            
        # Renormalize weights
        Weights /= np.sum(Weights)
        logger.info("Renormalized weights to " + str(np.sum(Weights)) + "\n")
        logger.info(("Physical observable '%s' uses denominator = %g %s\n" %
                     (observable.capitalize(), Denom,
                      self.points[0].ref["units"][self.observables.index(observable)])))

        # Grab calculated values        
        values = np.array([pt.data["values"][qid] for pt in self.points])
        errors = np.array([pt.data["errors"][qid] for pt in self.points])
        grads  = np.array([pt.data["grads"][qid] for pt in self.points])

        # Calculate objective term using Least-squares function. Evaluate using
        # Einstein summation: W is N-array, Delta is N-array and grads is
        # NxM-array, where N is number of points and M is number of parameters.
        #
        #     X_i   = W_i * Delta2_i (no summed indices)
        #     G_ij  = W_i * Delta_i * grads_ij (no summed indices)
        #     H_ijm = W_i * gradsT_jk * grads_lm (sum over k and l)
        #
        # Result: X is N-array, G is NxM-array and H is NxMxM-array.
        #
        Deltas = values - Exp
        Objs   = np.einsum("i,i->i", Weights, Deltas**2) / Denom / Denom
        Grads  = 2.0*np.einsum("i,i,ij->ij", Weights, Deltas, grads) / Denom / Denom
        Hess   = 2.0*np.einsum("i,jk,lm->ijm", Weights, grads.T, grads) / Denom / Denom
        
        # Average over all points
        Objective += np.sum(Objs, axis=0)
        Gradient  += np.sum(Grads, axis=0)
        Hessian   += np.sum(Hess, axis=0)
        
        # Store gradients and setup print map 
        GradMapPrint = [["#Point"] + self.FF.plist]

        for pt in self.points:
            temp  = pt.temperature
            press = pt.pressure
            GradMapPrint.append([' %8.2f %8.1f' % (temp, press)] +
                                ["% 9.3e" % i for i in grads[pt.idnr-1]])

        o = wopen('gradient_%s.dat' % observable)
        for line in GradMapPrint:
            print >> o, ' '.join(line)
        o.close()
        
        printer = OrderedDict([("    %-5d %-12.2f %-8.1f"
                                % (pt.idnr, pt.temperature, pt.pressure),
                                ("% -10.3f % -10.3f  +- %-8.3f % -8.3f % -9.5f % -9.5f"
                                 % (Exp[pt.idnr-1], values[pt.idnr-1],
                                    errors[pt.idnr-1], Deltas[pt.idnr-1],
                                    Weights[pt.idnr-1], Objs[pt.idnr-1])))
                                    for pt in self.points])
                
        return { "X": Objective, "G": Gradient, "H": Hessian, "info": printer }

    def get(self, mvals, AGrad=True, AHess=True):
        """Return the contribution to the total objective function. This is a
        weighted average of the calculated observables.

        Parameters
        ----------
        mvals : list
            Mathematical parameter values.
        AGrad : Boolean
            Switch to turn on analytic gradient.
        AHess : Boolean
            Switch to turn on analytic Hessian.

        Returns
        -------
        Answer : dict
            Contribution to the objective function. `Answer` is a dict with keys
            `X` for the objective function, `G` for its gradient and `H` for its
            Hessian.
                    
        """
        Answer   = {}

        Objective = 0.0
        Gradient  = np.zeros(self.FF.np)
        Hessian   = np.zeros((self.FF.np, self.FF.np))
        return { "X": Objective, "G": Gradient, "H": Hessian} 

        for pt in self.points:
            # Update data point with MD results
            self.retrieve(pt)

        obj        = OrderedDict()
        reweighted = []
        for q in self.observables:
            # Returns dict with keys "X"=objective term value, "G"=the
            # gradient, "H"=the hessian, and "info"=printed info about points
            obj[q] = self.objective_term(q)
        
            # Apply weights for observables (normalized)
            if obj[q]["X"] == 0:
                self.weights[q] = 0.0

            # Store weights sorted in the order of self.observables
            reweighted.append(self.weights[q])
        
        # Normalize weights
        reweighted  = np.array(reweighted)
        wtot        = np.sum(reweighted)
        reweighted  = reweighted/wtot if wtot > 0 else reweighted
         
        # Picks out the "X", "G" and "H" keys for the observables sorted in the
        # order of self.observables. Xs is N-array, Gs is NxM-array and Hs is
        # NxMxM-array, where N is number of observables and M is number of
        # parameters.
        Xs = np.array([dic["X"] for dic in obj.values()])
        Gs = np.array([dic["G"] for dic in obj.values()])
        Hs = np.array([dic["H"] for dic in obj.values()])
                                
        # Target contribution is (normalized) weighted averages of the
        # individual observable terms.
        Objective    = np.average(Xs, weights=(None if np.all(reweighted == 0) else \
                                               reweighted), axis=0)
        if AGrad:
            Gradient = np.average(Gs, weights=(None if np.all(reweighted == 0) else \
                                               reweighted), axis=0)
        if AHess:
            Hessian  = np.average(Hs, weights=(None if np.all(reweighted == 0) else \
                                               reweighted), axis=0)

        if not in_fd():
            # Store results to show with indicator() function
            self.Xp = {q : dic["X"] for (q, dic) in obj.items()}
            self.Wp = {q : reweighted[self.observables.index(q)]
                       for (q, dic) in obj.items()}
            self.Pp = {q : dic["info"] for (q, dic) in obj.items()}

            if AGrad:
                self.Gp = {q : dic["G"] for (q, dic) in obj.items()}

            self.Objective = Objective
        
        Answer = { "X": Objective, "G": Gradient, "H": Hessian }
        return Answer
    
# class Point --- data container
class Point(object):
    def __init__(self, idnr, label=None, refs=None, weights=None, names=None,
                 units=None, temperature=None, pressure=None, data=None):
        self.idnr        = idnr
        self.ref         = { "label"  : label,                    
                             "refs"   : refs,
                             "weights": weights,
                             "names"  : names,
                             "units"  : units }
        self.temperature = temperature
        self.pressure    = pressure
        self.data        = data if data is not None else {}
        
    def __str__(self):
        msg = []
        if self.temperature is None:
            msg.append("State: Unknown.")
        elif self.pressure is None:
            msg.append("State: Point " + str(self.idnr) + " at " +
                       str(self.temperature) + " K.")
        else:
            msg.append("State: Point " + str(self.idnr) + " at " +
                       str(self.temperature) + " K and " +
                       str(self.pressure) + " bar.")

        msg.append("Point " + str(self.idnr) + " reference data " + "-"*30)
        for key in self.ref:
            msg.append("  " + key.strip() + " = " + str(self.ref[key]).strip())
            
        msg.append("Point " + str(self.idnr) + " calculated data " + "-"*30)
        for key in self.data:
            msg.append("  " + key.strip() + " = " + str(self.data[key]).strip())

        return "\n".join(msg)


