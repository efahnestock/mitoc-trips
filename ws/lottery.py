from __future__ import unicode_literals

import random

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q

from ws.utils.dates import local_date, closest_wed_at_noon, jan_1
from ws.utils.signups import add_to_waitlist
from ws import models


def reciprocally_paired(participant):
    try:
        paired_par = participant.lotteryinfo.paired_with
    except ObjectDoesNotExist:  # No lottery info (paired_with default is None)
        return False

    try:
        return paired_par and paired_par.lotteryinfo.paired_with == participant
    except ObjectDoesNotExist:  # No lottery info for paired participant
        return False


def par_is_driver(participant):
    try:
        return participant.lotteryinfo.is_driver
    except AttributeError:  # No lottery form submission
        return False


def place_on_trip(signup):
    trip = signup.trip
    print "{} has {} slot(s), adding {}".format(trip, trip.open_slots,
                                                signup.participant)
    signup.on_trip = True
    signup.save()


class ParticipantRanker(object):
    """ Rank participants at a given point in time. """

    def __init__(self):
        self.today = local_date()
        self.jan_1st = jan_1()

    def get_prioritized_participants(self):
        """ Return ordered list of participants, ranked by:

        1. number of trips (fewer -> higher priority)
        2. affiliation (MIT affiliated is higher priority)
        3. 'flakiness' (more flakes -> lower priority
        """
        participants = models.Participant.objects.all()
        return sorted(participants, key=self.priority_key)

    def priority_key(self, participant):
        """ Return tuple for sorting participants. """
        flake_factor = self.get_flake_factor(participant)
        # If we use raw flake factor, participants who've been on trips
        # will have an advantage over those who've been on none
        flaky_or_neutral = max(flake_factor, 0)
        number_of_trips = self.number_ws_trips(participant)

        # First preference first (single-letter codes are old)
        ranked_affiliations = ['MU', 'MG', 'MA', 'M', 'NU', 'NG', 'S', 'NA', 'N']
        affiliation = ranked_affiliations.index(participant.affiliation)

        # Lower = higher in the list
        # Random float faily resolves ties without using database order
        return (flaky_or_neutral, number_of_trips, affiliation, random.random())

    def get_flake_factor(self, participant):
        """ Return a number indicating past "flakiness".

        A lower score indicates a more reliable participant.
        """
        score = 0
        for trip in self.past_ws_trips(participant):
            trip_feedback = participant.feedback_set.filter(trip=trip)
            if not trip_feedback.exists():
                continue
            # If any leader says they flaked, then assume a flake
            showed_up = all(feedback.showed_up for feedback in trip_feedback)
            score += 5 if not showed_up else -2
        return score

    def past_ws_trips(self, participant):
        """ Past Winter School trips participant has been on this year. """
        return participant.trip_set.filter(trip_date__gt=self.jan_1st,
                                           trip_date__lt=self.today,
                                           activity='winter_school')

    def number_ws_trips(self, participant):
        """ Number of trips the participant has been on this year.

        Leaders who've led more trips are given priority over others.
        (A dedicated MITOC leader should be given priority)
        """
        past_trips = self.past_ws_trips(participant)
        signups = participant.signup_set.filter(trip__in=past_trips, on_trip=True)
        return signups.count() - participant.trips_led.count()

    def lowest_non_driver(self, trip):
        """ Return the lowest priority non-driver on the trip. """
        accepted_signups = trip.signup_set.filter(on_trip=True)
        non_driver_kwargs = {'participant__lotteryinfo__car_status': 'none'}
        non_drivers = accepted_signups.filter(**non_driver_kwargs)
        return max(non_drivers, key=lambda signup: self.priority_key(signup.participant))


class LotteryRunner(ParticipantRanker):
    def __init__(self, *args, **kwargs):
        super(LotteryRunner, self).__init__(*args, **kwargs)
        self.participants_handled = {}  # Key: primary keys, gives boolean if handled

    def handled(self, participant):
        return self.participants_handled.get(participant.pk, False)

    def mark_handled(self, participant, handled=True):
        self.participants_handled[participant.pk] = handled

    def execute_lottery(self):
        self.assign_trips()
        self.free_for_all()

    def free_for_all(self):
        """ Make trips first-come, first-serve.

        Trips re-open Wednesday at noon, close at midnight on Thursday.
        """
        print "Making all lottery trips first-come, first-serve"
        ws_trips = models.Trip.objects.filter(activity='winter_school')
        noon = closest_wed_at_noon()
        for trip in ws_trips.filter(algorithm='lottery'):
            trip.make_fcfs(signups_open_at=noon)
            trip.save()

    def assign_trips(self):
        for participant in self.get_prioritized_participants():
            handling_text = "Handling {}".format(participant)
            print handling_text
            print '-' * len(handling_text)
            par_handler = ParticipantHandler(participant, self)
            par_handler.place_participant()
            print


class ParticipantHandler(object):
    """ Class to handle placement of a single participant or pair. """
    is_driver_q = Q(participant__lotteryinfo__car_status__in=['own', 'rent'])

    def __init__(self, participant, runner):
        self.participant = participant
        self.slots_needed = len(self.to_be_placed)
        self.runner = runner

    @property
    def is_driver(self):
        driver = par_is_driver(self.participant)
        paired_par = self.paired_par
        return driver or paired_par and par_is_driver(paired_par)

    @property
    def paired(self):
        return reciprocally_paired(self.participant)

    @property
    def paired_par(self):
        return self.paired and self.participant.lotteryinfo.paired_with

    @property
    def to_be_placed(self):
        if self.paired:
            return (self.participant, self.paired_par)
        else:
            return (self.participant,)

    @property
    def par_text(self):
        return " + ".join(map(unicode, self.to_be_placed))

    @property
    def future_signups(self):
        # Only consider lottery signups for future trips
        signups = self.participant.signup_set.filter(
            trip__trip_date__gt=self.runner.today,
            trip__algorithm='lottery',
            trip__activity='winter_school'
        )
        if self.paired:  # Restrict signups to those both signed up for
            signups = signups.filter(trip__in=self.paired_par.trip_set.all())
        return signups.order_by('order', 'time_created')

    def place_all_on_trip(self, signup):
        place_on_trip(signup)
        if self.paired:
            par_signup = models.SignUp.objects.get(participant=self.paired_par,
                                                   trip=signup.trip)
            place_on_trip(par_signup)

    def count_drivers_on_trip(self, trip):
        participant_drivers = trip.signup_set.filter(self.is_driver_q, on_trip=True)
        lottery_leaders = trip.leaders.filter(lotteryinfo__isnull=False)
        num_leader_drivers = sum(leader.lotteryinfo.is_driver
                                 for leader in lottery_leaders)
        return participant_drivers.count() + num_leader_drivers

    def placed_on_trip(self, signup):
        trip = signup.trip
        if trip.open_slots >= self.slots_needed:
            self.place_all_on_trip(signup)
            return True
        elif self.is_driver and not trip.open_slots and not self.paired:
            # A driver may displace somebody else
            # (but a couple with a driver cannot displace two people)
            if self.count_drivers_on_trip(trip) < 2:
                print "{} is full, but doesn't have two drivers".format(trip)
                print "Adding {} to '{}', as they're a driver".format(signup, trip)
                par_to_bump = self.runner.lowest_non_driver(trip)
                add_to_waitlist(par_to_bump, prioritize=True)
                signup.on_trip = True
                signup.save()
                return True

    def place_participant(self):
        if self.paired:
            print "{} is paired with {}".format(self.participant, self.paired_par)
            if not self.runner.handled(self.paired_par):
                print "Will handle signups when {} comes".format(self.paired_par)
                self.runner.mark_handled(self.participant)
                return
        if not self.future_signups:
            print "{} did not choose any trips this week".format(self.par_text)
            self.runner.mark_handled(self.participant)
            return

        # Try to place participants on their first choice available trip
        for signup in self.future_signups:
            if self.placed_on_trip(signup):
                break
            else:
                print "Can't place {} on {}".format(self.par_text, signup.trip)

        else:  # No trips are open
            print "None of {}'s trips are open.".format(self.par_text)
            favorite_trip = self.future_signups.first().trip
            for participant in self.to_be_placed:
                find_signup = Q(participant=participant, trip=favorite_trip)
                favorite_signup = models.SignUp.objects.get(find_signup)
                add_to_waitlist(favorite_signup)

        self.runner.mark_handled(self.participant)
