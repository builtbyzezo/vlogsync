"""CapCut 9.8 English desktop adapter. Every export is gated by observed UI state."""
from pathlib import Path
import re
import subprocess
import time

from desktop_controls import Box, Screen, focused_edit, foreground, pid, process_image, rect, title, user, wait_for
from deployment import CAPCUT_BUILD


class CapCut:
    def __init__(self, exporter, ocr):
        self.exporter = exporter
        self.ocr = ocr
        self.binary = exporter.LOCAL/'CapCut'/'Apps'/CAPCUT_BUILD/'CapCut.exe'
        if not self.binary.is_file():
            raise RuntimeError('CapCut 9.8.0.4213 is required to use the qualified desktop controls')

    def windows(self, name):
        return [h for h, p, t in self.exporter.windows()
                if t == name and process_image(p) == self.binary and rect(h).w > 0]

    def focus(self, hwnd):
        if process_image(pid(hwnd)) != self.binary:
            raise RuntimeError('Window does not belong to the qualified CapCut installation')
        if user.GetForegroundWindow() == hwnd:
            return
        if not foreground(hwnd):
            raise RuntimeError('Windows declined CapCut foreground access')

    def screen(self, hwnd):
        self.focus(hwnd)
        return Screen(hwnd, self.ocr)

    def get_window(self, name):
        return wait_for(lambda: self.current_window(name), f'the exact {name} window')

    def current_window(self, name):
        candidates = self.windows(name)
        fg = user.GetForegroundWindow()
        if name=='CapCut' and len(candidates)>1:
            # Notifications share the main title. Separate them using relative area,
            # independent of the monitor resolution and Windows display scaling.
            sizes = {h:r.w*r.h for h in candidates if (r := rect(h))}
            largest = max(sizes.values())
            primary = [h for h,area in sizes.items() if 4*area > largest]
            if fg in primary:
                return fg
            return primary[0] if len(primary)==1 else None
        if fg in candidates:
            return fg
        return candidates[0] if len(candidates) == 1 else None

    def home(self):
        screen = self.screen(self.get_window('CapCut'))
        screen.unique('Projects')
        screen.unique('Create project')
        return screen

    def leave_home(self):
        screen = self.home()
        states = ('Waiting to export', 'Exporting', 'Could not finish export')
        if any(screen.matches(state) for state in states):
            raise RuntimeError('CapCut has export activity. Leaving it open.')
        if any(self.exporter.ROOT.glob('*/.locked')):
            raise RuntimeError('A CapCut project remains locked. Leaving it open.')
        screen.check()
        if not user.PostMessageW(screen.window, 0x0010, 0, 0):
            raise RuntimeError('CapCut did not accept its normal Close request')
        def idle():
            try:
                self.exporter.draft.require_idle(self.exporter.ROOT)
            except RuntimeError:
                return False
            return True
        wait_for(idle, 'CapCut to close normally', seconds=45)

    def launch(self):
        if not self.environment_window():
            subprocess.Popen([str(self.exporter.CAPCUT)], cwd=self.exporter.CAPCUT.parent)
        return self.await_home()

    def environment_window(self):
        detector = self.binary.with_name('VEDetector.exe')
        matches = [h for h,p,t in self.exporter.windows()
                   if t=='Environment testing' and process_image(p)==detector]
        return matches[0] if len(matches)==1 else None

    def await_home(self):
        acknowledged = False
        def available():
            nonlocal acknowledged
            detector = self.environment_window()
            if detector and not acknowledged and user.GetForegroundWindow()==detector:
                screen = Screen(detector,self.ocr)
                if screen.matches('Your computer can run') and screen.matches('CapCut smoothly'):
                    bottom = Box(0,screen.bounds.h*2//3,screen.bounds.w,screen.bounds.h//3)
                    screen.click(screen.button('Confirm',bottom).center)
                    acknowledged = True
                return None
            window = self.current_window('CapCut')
            if window is None:
                return None
            try:
                screen = self.screen(window)
                if len(screen.matches('Projects'))==1 and len(screen.matches('Create project'))==1:
                    return screen
            except RuntimeError as exc:
                fg = user.GetForegroundWindow()
                if 'lost the foreground' in str(exc) and process_image(pid(fg)) == self.binary:
                    return None
                raise
            return None
        return wait_for(available, 'CapCut Home and its Projects section', seconds=85, poll=.6)

    def home_row(self, screen, name):
        # Rows and toolbar share text baselines. Check exact contiguous words in the Name column.
        projects = screen.unique('Projects')
        top=projects.y+projects.h+25
        area=Box(0,top,screen.bounds.w*2//3,screen.bounds.h-top)
        found=screen.matches(name,area=area)
        if found:
            return found
        left=max(0,projects.x-60)
        names=Box(left,top,screen.bounds.w//3,screen.bounds.h-top)
        for scale in (3,2,4):
            found=screen.enlarged_matches(name,names,scale=scale)
            if found:
                return found
        # Isolate candidate rows so small labels are not fused with grid text.
        prefix=name.split()[0].casefold()
        rows=[]
        for line in screen.lines:
            for word in line:
                if (word['t'].casefold()!=prefix or not names.x<=word['x']<names.x+names.w
                        or word['y']<top):
                    continue
                width=min(names.x+names.w-word['x'], max(110,round(len(name)*8)))
                strip=Box(max(0,word['x']-4),max(0,word['y']-6),width+4,word['h']+18)
                for scale in (2,3,4):
                    match=screen.enlarged_matches(name,strip,scale=scale)
                    if match:
                        rows.extend(match)
                        break
        return rows

    def search(self, name):
        screen = self.home()
        for _ in range(2):
            projects=screen.unique('Projects')
            toolbar=Box(screen.bounds.w//2,max(0,projects.y-27),screen.bounds.w//2,67)
            trash=screen.button('Trash',toolbar,scale=2)
            sync=screen.button('Project Sync',toolbar,scale=2)
            # Search and its clear button occupy the same verified toolbar slot.
            scale = min(2, max(.75, (sync.x-trash.x)/90))
            screen.click((round(trash.x-118*scale), trash.center[1]))
            time.sleep(.35)
            screen = self.home()
            area = Box(trash.x-round(460*scale),trash.y-22,round(430*scale),62)
            hints = screen.matches('Search projects',area=area) or screen.matches('Search',area=area)
            if len(hints)==1:
                break
        else:
            raise RuntimeError('CapCut project search field could not be verified')
        screen.click(hints[0].center)
        screen.text(name)
        time.sleep(.55)
        result = self.home()
        hits = self.home_row(result, name)
        if len(hits)==1:
            return result,hits[0].center
        if hits:
            raise RuntimeError('CapCut returned more than one matching title')
        # Some all-numeric titles are skipped by OCR. Navigate the single search
        # result only, then require the exact project lock and native dialog title.
        duration = result.unique('Duration')
        size = result.unique('Size',area=Box(result.bounds.w//2,duration.y-15,
                                              result.bounds.w//2,50))
        def rows(pattern, left, right):
            return [round(w['y']+w['h']/2) for line in result.lines for w in line
                    if left<=w['x']<=right and w['y']>=duration.y+30 and re.fullmatch(pattern,w['t'])]
        dates = rows(r'(?:\d{1,2}/){1,2}\d{1,4}',result.bounds.w*9//10,result.bounds.w)
        sizes = rows(r'\d+(?:[.,]\d+)?[KMG]B?',size.x-28,size.x+62)
        times = rows(r'(?:\d{1,2}:){1,2}\d{2}',duration.x-32,duration.x+75)
        matched = [y for y in dates if any(abs(y-n)<=6 for n in [*sizes,*times])]
        if len(matched)!=1 or len(dates)!=1:
            raise RuntimeError(f'CapCut did not show one unambiguous result for {name}')
        label = result.unique('Name',area=Box(300,duration.y-15,result.bounds.w//3,50))
        return result,(label.x+50,matched[0])

    def open_draft(self, name):
        result_title = 'Export-'+name
        if not self.windows(result_title):
            screen = self.navigate_home()
            hits = self.home_row(screen, name)
            if len(hits)>1:
                raise RuntimeError(f'Expected one exact CapCut row for {name}')
            if hits:
                point = hits[0].center
            else:
                screen, point = self.search(name)
            screen.click(point, double=True)
        def opened():
            if (self.exporter.ROOT/name/'.locked').is_file():
                return True
            if self.windows(result_title):
                result = self.dialog(result_title)
                if result.matches('Open folder') and result.matches('Open draft'):
                    area = Box(result.bounds.w//2,result.bounds.h-112,result.bounds.w//2,112)
                    result.click(result.button('Open draft',area).center)
                    return wait_for(lambda: (self.exporter.ROOT/name/'.locked').is_file(),
                                    f'project lock after reopening the completed result for {name}',seconds=20)
            return None
        wait_for(opened,f'project lock or completed result for {name}',seconds=20)
        def editor():
            try:
                observed = self.screen(self.get_window('CapCut'))
            except RuntimeError as error:
                if ('moved or lost the foreground' in str(error) and
                        process_image(pid(user.GetForegroundWindow())) == self.binary):
                    return None
                raise
            heading = Box(0, 0, observed.bounds.w, min(65, observed.bounds.h))
            return observed if (observed.matches(name, area=heading) or
                                self.editor_for(observed,name)) else None
        return wait_for(editor, f'editor heading for {name}', seconds=60)

    def editor_for(self, screen, name):
        locks = [candidate.parent.name for candidate in self.exporter.ROOT.glob('*/.locked')]
        return (locks == [name] and screen.matches('Menu',area=Box(0,0,screen.bounds.w,65))
                and screen.matches('Player-Timeline',prefix=True))

    def navigate_home(self):
        def open_home():
            locks = [p.parent.name for p in self.exporter.ROOT.glob('*/.locked')]
            if not locks:
                # During launch and dialog teardown a Home window can repaint.
                return True
            if len(locks)!=1 or not locks[0].startswith('WVE '):
                raise RuntimeError('An unrelated or ambiguous CapCut editor is open')
            fg = user.GetForegroundWindow()
            try:
                main = self.screen(self.get_window('CapCut'))
                if main.matches('Create project') and main.matches('Projects'):
                    return not any(self.exporter.ROOT.glob('*/.locked'))
                if main.matches('Back to home page'):
                    main.click(main.unique('Back to home page').center)
                    return True
                heading=Box(0,0,main.bounds.w,65)
                if self.editor_for(main,locks[0]) or (
                        main.matches(locks[0],area=heading) and main.matches('Player-Timeline',prefix=True)):
                    top = Box(0,0,main.bounds.w,65)
                    menus=main.matches('Menu',area=top)
                    if len(menus)==1:
                        control=menus[0]
                    else:
                        top=Box(0,0,main.bounds.w//4,65)
                        control=main.button('Menu',top,scale=3)
                    main.click(control.center)
                    time.sleep(.35)
                    return None
            except RuntimeError as error:
                if ('moved or lost the foreground' not in str(error)
                        and 'lost focus or moved before input' not in str(error)):
                    raise
                active = user.GetForegroundWindow()
                if active and active!=fg and process_image(pid(active))!=self.binary:
                    raise
            return None
        wait_for(open_home,'the verified CapCut editor-to-Home transition',seconds=25,poll=.55)
        return self.await_home()

    def export_dialog(self, entry):
        expected = 'Export-' + entry['title']
        if (self.exporter.ROOT/entry['title']/'.locked').exists():
            if self.windows(expected):
                return self.dialog(expected)
            editor = self.screen(self.get_window('CapCut'))
            top = Box(0,0,editor.bounds.w,65)
            if not editor.matches(entry['title'],area=top) and not self.editor_for(editor,entry['title']):
                raise RuntimeError('The editor cannot be linked to the expected project lock')
            return self.open_dialog(entry,editor)
        editor = self.open_draft(entry['title'])
        return self.open_dialog(entry, editor)

    def open_dialog(self, entry, editor):
        self.exporter.open_export(entry['date'], editor.window)
        expected = 'Export-' + entry['title']
        try:
            # CapCut can prepare a large draft's export dialog while native jobs render.
            wait_for(lambda: self.windows(expected), 'the Ctrl+E export dialog', seconds=30)
        except RuntimeError:
            try:
                observed = self.screen(editor.window)
            except RuntimeError:
                # The dialog can take the foreground during an editor capture.
                if self.windows(expected):
                    return self.dialog(expected)
                raise
            top = Box(observed.bounds.w*3//4, 0, observed.bounds.w//4, 62)
            observed.click(observed.button('Export',top).center)
        return self.dialog(expected)

    def dialog(self, expected):
        def settled():
            window = self.current_window(expected)
            if window is None:
                return None
            try:
                frame = self.screen(window)
            except RuntimeError as error:
                if ('lost the foreground' in str(error) and
                        process_image(pid(user.GetForegroundWindow())) == self.binary):
                    return None
                raise
            self.check_warning(frame)
            return frame
        return wait_for(settled,f'the settled {expected} dialog',seconds=7)

    @staticmethod
    def check_warning(screen):
        terms = ('Export anyway', 'missing media', 'black frames', 'link media', 'media lost')
        if any(screen.matches(term) for term in terms):
            raise RuntimeError('CapCut reported missing media or black frames. Export was not confirmed.')

    @staticmethod
    def reference(screen, label):
        area=Box(screen.bounds.w//2-20,45,screen.bounds.w//7+20,screen.bounds.h-105)
        matches=screen.matches(label,area=area)
        if len(matches)==1:
            return matches[0]
        if matches:
            return screen.unique(label,area=area)
        return screen.button(label,area,scale=3)

    def value(self, screen, label, value):
        y = self.reference(screen, label).center[1]
        right = screen.bounds.w//2 + screen.bounds.w//8
        if (label,value)==('Bit rate','Custom') and screen.matches('CBR',prefix=True) and screen.matches('VBR',prefix=True):
            return True
        area=Box(right,y-13,screen.bounds.w-right,26)
        if screen.matches(value,area=area):
            return True
        try:
            screen.button(value,area,scale=3)
        except RuntimeError as error:
            if not str(error).startswith('Unable to uniquely verify'):
                raise
            return False
        return True

    def choose(self, screen, label, value):
        if self.value(screen, label, value):
            return screen
        expected = title(screen.window)
        row = self.reference(screen, label)
        screen.click((round(screen.bounds.w*.76), row.center[1]))
        def dropdown():
            h = user.GetForegroundWindow()
            if h != screen.window and user.GetWindow(h,4)==screen.window and process_image(pid(h))==self.binary:
                return h
            return None
        def stable_menu():
            h = dropdown()
            if h is None:
                return None
            try:
                return self.screen(h)
            except RuntimeError as error:
                if ('moved or lost the foreground' in str(error) and
                        process_image(pid(user.GetForegroundWindow())) == self.binary):
                    return None
                raise
        def selectable():
            menu = stable_menu()
            if menu is None:
                return None
            options = menu.matches(value)
            if value=='HEVC':
                options = [option for option in options if '422' not in menu.row_text(option)]
                if len(options)!=1:
                    menu_area=Box(0,0,menu.bounds.w,menu.bounds.h)
                    candidates=menu.enlarged_matches('HEVC',menu_area,scale=2)
                    label422=menu.enlarged_matches('422',menu_area,scale=2)
                    if len(candidates)==2 and len(label422)==1:
                        options=[option for option in candidates
                                 if abs(option.center[1]-label422[0].center[1])>10]
            if len(options)!=1 and value!='HEVC':
                area=Box(0,0,menu.bounds.w,menu.bounds.h)
                for scale in (2,3,4):
                    options=menu.enlarged_matches(value,area,scale=scale)
                    if len(options)==1:
                        break
            if len(options)==1 and dropdown()==menu.window and menu.visible_at(options[0].center):
                return menu,options[0].center
            return None
        menu,point = wait_for(selectable,f'the unobstructed {label} option',seconds=16)
        menu.click(point)
        def confirmed():
            if dropdown():
                return None
            result = self.dialog(expected)
            return result if self.value(result,label,value) else None
        return wait_for(confirmed,f'CapCut to retain {label} {value}',seconds=8)

    def configure(self, screen, kind):
        expected = title(screen.window)
        codec, size = ('HEVC', '1080P') if kind == 'Master' else ('H.264', '720P')
        screen = self.choose(screen, 'Resolution', size)
        screen = self.choose(screen, 'Codec', codec)
        screen = self.choose(screen, 'Format', 'mp4')
        if not self.value(screen, 'Frame rate', '29.97fps'):
            screen = self.choose(screen, 'Frame rate', '29.97fps')
        screen = self.choose(screen, 'Bit rate', 'Recommended' if kind == 'Master' else 'Custom')
        if kind == 'Proxy':
            bit_rate=self.reference(screen,'Bit rate')
            band=Box(screen.bounds.w//2,bit_rate.y-8,screen.bounds.w//2,125)
            unit=screen.button('Kbps',band,scale=2)
            area = Box(screen.bounds.w*5//8,unit.y-10,unit.x-screen.bounds.w*5//8,42)
            def exact_rate(view):
                return len(view.enlarged_matches('3000',area,scale=2))==1
            if not exact_rate(screen):
                screen.click((unit.x-round(screen.bounds.w*.09), unit.center[1]))
                screen.text('3000')
                time.sleep(.25)
                screen.click(self.reference(screen,'Bit rate').center)
            def confirmed():
                current = self.dialog(expected)
                return current if exact_rate(current) else None
            screen = wait_for(confirmed,'the 3000 Kbps proxy bitrate field',seconds=6)
        return screen

    def destination(self, screen, directory):
        label = self.reference(screen, 'Export to')
        # Adjacent folder icon opens a standard Windows folder picker.
        screen.click((round(screen.bounds.w*.955), label.center[1]))
        return self.pick_destination(title(screen.window), directory)

    def pick_destination(self, expected, directory):
        picker = self.screen(self.get_window('Select exporting path'))
        bottom = Box(0, picker.bounds.h*4//5, picker.bounds.w, picker.bounds.h//5)
        name = picker.unique('Folder', area=Box(0, bottom.y, picker.bounds.w//3, bottom.h))
        picker.click((name.x + name.w + 65, name.center[1]))
        picker.text(str(directory))
        wait_for(lambda: Path(focused_edit(picker.window)) == directory,
                 'the exact typed output path in the folder picker', seconds=4)
        picker = self.screen(picker.window)
        picker.click(picker.unique('Select Folder', area=bottom).center)
        wait_for(lambda: not self.windows('Select exporting path'), 'the folder picker to close', seconds=10)
        return self.dialog(expected)

    def prepare_export(self, item, kind, path):
        expected = 'Export-'+item['title']
        chosen = None
        if self.windows('Select exporting path') and self.windows(expected):
            chosen = self.pick_destination(expected,path.parent)
        screen = chosen or self.export_dialog(item)
        if not self.is_setup(screen):
            screen = self.await_setup(expected)
        screen = self.configure(screen, kind)
        if not chosen:
            screen = self.destination(screen, path.parent)
        label = self.reference(screen, 'Name')
        screen.click((round(screen.bounds.w*.76), label.center[1]))
        screen.text(path.stem)
        time.sleep(.3)
        screen.click(label.center)
        screen = self.dialog(title(screen.window))
        name_area=Box(screen.bounds.w//2,label.y-15,screen.bounds.w//2,42)
        if len(screen.matches(path.stem,area=name_area))!=1:
            screen.button(path.stem,name_area,scale=3)
        if path.exists():
            raise FileExistsError(path)
        return screen

    def is_setup(self,screen):
        try:
            positions=[self.reference(screen,label).center[1]
                       for label in ('Export to','Resolution','Codec','Frame rate')]
        except RuntimeError as error:
            if str(error).startswith('Unable to uniquely verify'):
                return False
            raise
        return positions==sorted(set(positions))

    def await_setup(self, expected):
        def ready():
            if not self.current_window(expected):
                return None
            screen = self.dialog(expected)
            return screen if self.is_setup(screen) else None
        return wait_for(ready,'the fully populated CapCut export settings',seconds=20)

    def cancel_unsubmitted_setup(self, item):
        expected = 'Export-'+item['title']
        if self.current_window(expected):
            screen = self.await_setup(expected)
            area = Box(screen.bounds.w//2,screen.bounds.h-110,screen.bounds.w//2,110)
            screen.click(screen.button('Cancel',area).center)
            wait_for(lambda: not self.windows(expected),'the unsubmitted setup dialog to close')

    def queued(self, screen, expected):
        def progress():
            try:
                new = self.dialog(expected)
            except RuntimeError as error:
                fg = user.GetForegroundWindow()
                if ('moved or lost the foreground' in str(error) and
                        process_image(pid(fg)) == self.binary and title(fg) in (expected,'CapCut')):
                    return None
                raise
            if (new.matches('Waiting to export') or new.matches('Exporting') or
                    self.finished(new)):
                return new
            return None
        return wait_for(progress, 'CapCut to accept the project into its native queue', seconds=15)

    @staticmethod
    def finished(screen):
        area=Box(0,max(0,screen.bounds.h-125),screen.bounds.w, min(125,screen.bounds.h))
        return bool(screen.matches('Open folder') or
                    len(screen.enlarged_matches('Open folder',area,scale=2))==1)

    def return_home(self, screen):
        expected = title(screen.window)
        def ready():
            if not self.windows(expected):
                return None
            try:
                view = self.dialog(expected)
                area = Box(view.bounds.w//2,view.bounds.h-125,view.bounds.w//2,125)
                finished = self.finished(view)
                if finished:
                    point = view.button('Close',area)
                else:
                    cancel = view.button('Cancel',area)
                    width = view.bounds.w//6
                    target = Box(cancel.x-width,cancel.y-21,width-14,cancel.h+42)
                    point = view.button('Back to home',target,scale=4)
                return view,point,finished
            except RuntimeError as error:
                if ('Unable to uniquely verify' in str(error) or
                        ('moved or lost the foreground' in str(error) and
                         process_image(pid(user.GetForegroundWindow())) == self.binary)):
                    return None
                raise
        view,point,finished = wait_for(ready,'a verified export result or Back to home control',seconds=15)
        view.click(point.center)
        wait_for(lambda: not self.windows(expected),'the export dialog to close')
        # The native background-queue button already starts the Home transition.
        # Sending Editor menu input during that handoff can race the window switch.
        return self.navigate_home() if finished else self.await_home()

    def restore_master(self, item):
        def close(dialog):
            lower = Box(dialog.bounds.w//2, dialog.bounds.h-95, dialog.bounds.w//2, 95)
            dialog.click(dialog.button('Cancel', lower).center)
        screen = self.configure(self.export_dialog(item), 'Master')
        close(screen)
        wait_for(lambda: not self.windows('Export-'+item['title']), 'preset dialog to close')
        editor = self.screen(self.get_window('CapCut'))
        if not self.editor_for(editor,item['title']):
            editor.unique(item['title'], area=Box(0,0,editor.bounds.w,65))
        reopened = self.open_dialog(item,editor)
        expected = {'Resolution':'1080P','Codec':'HEVC','Format':'mp4','Frame rate':'29.97fps','Bit rate':'Recommended'}
        persists = all(self.value(reopened, label, value) for label,value in expected.items())
        close(reopened)
        if not persists:
            raise RuntimeError('CapCut did not persist the restored 1080p HEVC preset after closing the dialog')
